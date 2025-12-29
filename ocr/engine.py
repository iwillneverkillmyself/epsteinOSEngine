"""OCR engine for text extraction with bounding boxes.

Supports multiple backends:
- PaddleOCR (recommended) - State-of-the-art PP-OCRv4 models
- EasyOCR - Good for handwritten text
- Tesseract - Traditional OCR, good fallback

All engines return word-level bounding boxes and confidence scores
for downstream search and highlighting.
"""
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import logging
from PIL import Image
import numpy as np
from config import Config
from ocr.preprocess import (
    build_ocr_variants, 
    enhance_for_ocr, 
    deskew_image,
    _ensure_rgb,
    _to_gray,
    OCRVariant
)

logger = logging.getLogger(__name__)


class OCREngine:
    """Base OCR engine interface."""
    
    def extract_text(self, image_path: Path) -> Dict:
        """
        Extract text from image with bounding boxes.
        
        Returns:
            {
                'text': str,
                'word_boxes': List[Dict],  # [{text, x, y, width, height, confidence}]
                'confidence': float,
                'engine': str,  # Which engine was used
                'metadata': dict  # Additional info (angle detected, etc.)
            }
        """
        raise NotImplementedError


class PaddleOCREngine(OCREngine):
    """
    PaddleOCR-based engine using PP-OCRv4 models.
    
    Features:
    - High-accuracy text detection (DB algorithm)
    - Angle classification (handles rotated text)
    - State-of-the-art recognition accuracy
    - Supports 100+ languages
    
    Best for: Maximum accuracy on scanned documents, forms, mixed layouts
    """
    
    def __init__(self, 
                 languages: List[str] = None,
                 use_gpu: bool = False,
                 use_angle_cls: bool = True):
        self.languages = languages or ['en']
        self.use_gpu = use_gpu
        self.use_angle_cls = use_angle_cls
        self._ocr = None
    
    @property
    def ocr(self):
        """Lazy load PaddleOCR instance."""
        if self._ocr is None:
            try:
                from paddleocr import PaddleOCR
                
                # Map language codes
                lang = self._map_language(self.languages[0] if self.languages else 'en')
                
                logger.info(f"Initializing PaddleOCR with lang={lang}, GPU={self.use_gpu}, angle_cls={self.use_angle_cls}")
                
                # Initialize with accuracy-focused parameters
                self._ocr = PaddleOCR(
                    use_angle_cls=self.use_angle_cls,
                    lang=lang,
                    use_gpu=self.use_gpu,
                    show_log=False,
                    # Detection parameters for better accuracy
                    det_db_thresh=Config.PADDLE_DET_DB_THRESH,
                    det_db_box_thresh=Config.PADDLE_DET_DB_BOX_THRESH,
                    det_db_unclip_ratio=Config.PADDLE_DET_DB_UNCLIP_RATIO,
                    det_limit_side_len=Config.PADDLE_DET_LIMIT_SIDE_LEN,
                    # Recognition parameters
                    rec_batch_num=Config.PADDLE_REC_BATCH_NUM,
                    drop_score=Config.PADDLE_DROP_SCORE,
                    # Use PP-OCRv4 models (best accuracy)
                    ocr_version='PP-OCRv4',
                )
                
            except ImportError:
                raise ImportError(
                    "PaddleOCR not installed. Install with: "
                    "pip install paddlepaddle paddleocr"
                )
        return self._ocr
    
    def _map_language(self, lang: str) -> str:
        """Map language codes to PaddleOCR format."""
        # PaddleOCR uses specific language codes
        lang_map = {
            'en': 'en',
            'eng': 'en',
            'english': 'en',
            'ch': 'ch',
            'chinese': 'ch',
            'fr': 'fr',
            'french': 'fr',
            'de': 'german',
            'german': 'german',
            'es': 'es',
            'spanish': 'es',
            'pt': 'pt',
            'portuguese': 'pt',
            'it': 'it',
            'italian': 'it',
            'ru': 'ru',
            'russian': 'ru',
            'ar': 'ar',
            'arabic': 'ar',
            'ja': 'japan',
            'japanese': 'japan',
            'ko': 'korean',
            'korean': 'korean',
            'latin': 'latin',
        }
        return lang_map.get(lang.lower(), 'en')
    
    def extract_text(self, image_path: Path) -> Dict:
        """
        Extract text using PaddleOCR with multi-pass preprocessing.
        
        Applies best-practice preprocessing and runs multiple passes
        to maximize text detection accuracy.
        """
        try:
            # Read and prepare image
            image = Image.open(image_path).convert("RGB")
            base_img = np.array(image)
            
            # Build preprocessing variants if enabled
            if Config.OCR_PREPROCESS:
                variants = build_ocr_variants(
                    base_img, 
                    scales=Config.OCR_SCALES,
                    deskew=Config.OCR_DESKEW,
                    max_variants=6
                )
            else:
                # Just use original and enhanced
                enhanced = enhance_for_ocr(base_img)
                variants = [
                    OCRVariant(name="original", image=base_img, scale=1.0),
                    OCRVariant(name="enhanced", image=enhanced, scale=1.0),
                ]
            
            best = {
                'text': '',
                'word_boxes': [],
                'confidence': 0.0,
                'engine': 'paddleocr',
                'metadata': {}
            }
            best_score = -1.0
            
            # Try each variant
            for v in variants:
                try:
                    result = self._run_ocr_pass(v.image, v.scale, v.name)
                    
                    # Score: prefer longer text with higher confidence
                    text_len = len(result.get('text', ''))
                    conf = result.get('confidence', 0.0)
                    score = (text_len + 1) * (0.3 + conf)
                    
                    if text_len > 0 and score > best_score:
                        best_score = score
                        best = result
                        best['metadata']['variant'] = v.name
                        best['metadata']['scale'] = v.scale
                        if v.rotation_angle != 0:
                            best['metadata']['deskew_angle'] = v.rotation_angle
                            
                except Exception as e:
                    logger.warning(f"PaddleOCR pass failed for variant {v.name}: {e}")
                    continue
            
            return best
            
        except Exception as e:
            logger.exception(f"Error in PaddleOCR extraction for {image_path}: {e}")
            return {
                'text': '',
                'word_boxes': [],
                'confidence': 0.0,
                'engine': 'paddleocr',
                'metadata': {'error': str(e)}
            }
    
    def _run_ocr_pass(self, img: np.ndarray, scale: float, variant_name: str) -> Dict:
        """Run a single OCR pass on an image."""
        # PaddleOCR expects BGR or path
        if img.ndim == 3 and img.shape[2] == 3:
            # Convert RGB to BGR for PaddleOCR
            import cv2
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        else:
            img_bgr = img
        
        # Run OCR
        results = self.ocr.ocr(img_bgr, cls=self.use_angle_cls)
        
        if not results or not results[0]:
            return {'text': '', 'word_boxes': [], 'confidence': 0.0, 'engine': 'paddleocr'}
        
        # Parse results
        word_boxes = []
        texts = []
        confidences = []
        
        for line in results[0]:
            bbox_points = line[0]  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            text = line[1][0]
            confidence = line[1][1]
            
            # Calculate bounding box from quadrilateral
            x_coords = [p[0] for p in bbox_points]
            y_coords = [p[1] for p in bbox_points]
            
            x = min(x_coords) / scale
            y = min(y_coords) / scale
            width = (max(x_coords) - min(x_coords)) / scale
            height = (max(y_coords) - min(y_coords)) / scale
            
            word_boxes.append({
                'text': text,
                'x': float(x),
                'y': float(y),
                'width': float(width),
                'height': float(height),
                'confidence': float(confidence),
                # Store original quadrilateral for precise highlighting
                'quad': [[p[0]/scale, p[1]/scale] for p in bbox_points]
            })
            
            texts.append(text)
            confidences.append(confidence)
        
        combined_text = ' '.join(texts).strip()
        avg_confidence = float(np.mean(confidences)) if confidences else 0.0
        
        return {
            'text': combined_text,
            'word_boxes': word_boxes,
            'confidence': avg_confidence,
            'engine': 'paddleocr',
            'metadata': {'variant': variant_name}
        }


class EasyOCREngine(OCREngine):
    """EasyOCR-based OCR engine (good for handwritten text)."""
    
    def __init__(self, languages: List[str] = None, gpu: bool = False):
        self.languages = languages or ['en']
        self.gpu = gpu
        self._reader = None
    
    @property
    def reader(self):
        """Lazy load EasyOCR reader."""
        if self._reader is None:
            # Compatibility shim:
            # Pillow>=10 removed Image.ANTIALIAS, but EasyOCR<=1.7.0 still references it.
            # This restores the attribute so EasyOCR doesn't crash.
            from PIL import Image as PILImage
            if not hasattr(PILImage, "ANTIALIAS") and hasattr(PILImage, "Resampling"):
                PILImage.ANTIALIAS = PILImage.Resampling.LANCZOS

            import easyocr
            logger.info(f"Initializing EasyOCR with languages: {self.languages}, GPU: {self.gpu}")
            self._reader = easyocr.Reader(self.languages, gpu=self.gpu)
        return self._reader
    
    def extract_text(self, image_path: Path) -> Dict:
        """Extract text using EasyOCR."""
        try:
            # Read image
            image = Image.open(image_path).convert("RGB")
            base_img = np.array(image)

            variants = (
                build_ocr_variants(base_img, scales=Config.OCR_SCALES, deskew=Config.OCR_DESKEW)
                if Config.OCR_PREPROCESS
                else [OCRVariant(name="rgb", image=base_img, scale=1.0)]
            )

            best = {"text": "", "word_boxes": [], "confidence": 0.0, "engine": "easyocr", "metadata": {}}
            best_score = -1.0

            # Try multiple OCR passes with different preprocessing and scaling.
            for v in variants:
                results = self.reader.readtext(
                    v.image,
                    detail=1,
                    paragraph=False,
                    decoder="beamsearch",
                    text_threshold=Config.EASYOCR_TEXT_THRESHOLD,
                    low_text=Config.EASYOCR_LOW_TEXT,
                    link_threshold=Config.EASYOCR_LINK_THRESHOLD,
                    canvas_size=Config.EASYOCR_CANVAS_SIZE,
                    mag_ratio=Config.EASYOCR_MAG_RATIO,
                )
            
                # Extract text and bounding boxes
                full_text = []
                word_boxes = []
                confidences = []

                for (bbox, text, confidence) in results:
                    x_coords = [point[0] for point in bbox]
                    y_coords = [point[1] for point in bbox]

                    x = min(x_coords) / float(v.scale)
                    y = min(y_coords) / float(v.scale)
                    width = (max(x_coords) - min(x_coords)) / float(v.scale)
                    height = (max(y_coords) - min(y_coords)) / float(v.scale)

                    word_boxes.append({
                        'text': text,
                        'x': float(x),
                        'y': float(y),
                        'width': float(width),
                        'height': float(height),
                        'confidence': float(confidence)
                    })

                    full_text.append(text)
                    confidences.append(confidence)

                combined_text = ' '.join(full_text).strip()
                avg_confidence = float(np.mean(confidences)) if confidences else 0.0

                # Score prefers longer text, with a boost for confidence.
                score = (len(combined_text) + 1) * (0.25 + avg_confidence)
                if combined_text and score > best_score:
                    best_score = score
                    best = {
                        'text': combined_text, 
                        'word_boxes': word_boxes, 
                        'confidence': avg_confidence,
                        'engine': 'easyocr',
                        'metadata': {'variant': v.name}
                    }

            return best
            
        except Exception as e:
            logger.exception(f"Error in EasyOCR extraction for {image_path}: {e}")
            return {
                'text': '',
                'word_boxes': [],
                'confidence': 0.0,
                'engine': 'easyocr',
                'metadata': {'error': str(e)}
            }


class TesseractEngine(OCREngine):
    """Tesseract-based OCR engine."""
    
    def __init__(self, languages: str = 'eng'):
        self.languages = languages
        try:
            import pytesseract
            self.pytesseract = pytesseract
        except ImportError:
            raise ImportError("pytesseract not installed. Install with: pip install pytesseract")
    
    def extract_text(self, image_path: Path) -> Dict:
        """Extract text using Tesseract."""
        try:
            image = Image.open(image_path).convert("RGB")
            base_img = np.array(image)
            variants = (
                build_ocr_variants(base_img, scales=Config.OCR_SCALES, deskew=Config.OCR_DESKEW)
                if Config.OCR_PREPROCESS
                else [OCRVariant(name="rgb", image=base_img, scale=1.0)]
            )

            best = {"text": "", "word_boxes": [], "confidence": 0.0, "engine": "tesseract", "metadata": {}}
            best_score = -1.0

            # Try a couple of psm modes for robustness
            psm_modes = [Config.TESSERACT_PSM, "11"] if Config.TESSERACT_PSM != "11" else ["11", "6"]

            for v in variants:
                pil = Image.fromarray(v.image)
                for psm in psm_modes:
                    cfg = f"--oem 3 --psm {psm}"
                    data = self.pytesseract.image_to_data(
                        pil,
                        lang=self.languages,
                        config=cfg,
                        output_type=self.pytesseract.Output.DICT
                    )

                    word_boxes = []
                    texts = []
                    confidences = []

                    n_boxes = len(data['text'])
                    for i in range(n_boxes):
                        text = (data['text'][i] or "").strip()
                        if not text:
                            continue
                        conf = float(data['conf'][i]) if data['conf'][i] != -1 else 0.0
                        word_boxes.append({
                            'text': text,
                            'x': float(data['left'][i]) / float(v.scale),
                            'y': float(data['top'][i]) / float(v.scale),
                            'width': float(data['width'][i]) / float(v.scale),
                            'height': float(data['height'][i]) / float(v.scale),
                            'confidence': conf
                        })
                        texts.append(text)
                        if conf > 0:
                            confidences.append(conf)

                    combined_text = ' '.join(texts).strip()
                    avg_confidence = float(np.mean(confidences)) if confidences else 0.0
                    score = (len(combined_text) + 1) * (0.25 + (avg_confidence / 100.0))
                    if combined_text and score > best_score:
                        best_score = score
                        best = {
                            'text': combined_text, 
                            'word_boxes': word_boxes, 
                            'confidence': avg_confidence,
                            'engine': 'tesseract',
                            'metadata': {'variant': v.name, 'psm': psm}
                        }

            return best
            
        except Exception as e:
            logger.exception(f"Error in Tesseract extraction for {image_path}: {e}")
            return {
                'text': '',
                'word_boxes': [],
                'confidence': 0.0,
                'engine': 'tesseract',
                'metadata': {'error': str(e)}
            }


class EnsembleOCREngine(OCREngine):
    """
    Ensemble OCR engine that combines multiple engines for best results.
    
    Runs multiple engines and selects the best result based on
    confidence and text length.
    """
    
    def __init__(self, engines: List[OCREngine] = None):
        self.engines = engines or []
    
    def extract_text(self, image_path: Path) -> Dict:
        """Run all engines and return best result."""
        best = {
            'text': '',
            'word_boxes': [],
            'confidence': 0.0,
            'engine': 'ensemble',
            'metadata': {}
        }
        best_score = -1.0
        
        for engine in self.engines:
            try:
                result = engine.extract_text(image_path)
                text_len = len(result.get('text', ''))
                conf = result.get('confidence', 0.0)
                score = (text_len + 1) * (0.3 + conf)
                
                if text_len > 0 and score > best_score:
                    best_score = score
                    best = result
                    best['metadata']['selected_engine'] = result.get('engine', 'unknown')
                    
            except Exception as e:
                logger.warning(f"Engine {engine.__class__.__name__} failed: {e}")
                continue
        
        return best


def get_ocr_engine() -> OCREngine:
    """Factory function to get the configured OCR engine."""
    engine_name = Config.OCR_ENGINE.lower()
    
    if engine_name == 'textract':
        # AWS Textract - high accuracy cloud OCR
        from ocr.textract import TextractEngine
        return TextractEngine()
    elif engine_name == 'paddleocr':
        return PaddleOCREngine(
            languages=Config.OCR_LANGUAGES,
            use_gpu=Config.OCR_GPU,
            use_angle_cls=Config.PADDLE_USE_ANGLE_CLS
        )
    elif engine_name == 'easyocr':
        return EasyOCREngine(
            languages=Config.OCR_LANGUAGES,
            gpu=Config.OCR_GPU
        )
    elif engine_name == 'tesseract':
        return TesseractEngine(languages=','.join(Config.OCR_LANGUAGES))
    elif engine_name == 'ensemble':
        # Use all engines and pick best result
        engines = [
            PaddleOCREngine(
                languages=Config.OCR_LANGUAGES,
                use_gpu=Config.OCR_GPU,
                use_angle_cls=Config.PADDLE_USE_ANGLE_CLS
            ),
            EasyOCREngine(
                languages=Config.OCR_LANGUAGES,
                gpu=Config.OCR_GPU
            ),
        ]
        return EnsembleOCREngine(engines=engines)
    else:
        raise ValueError(f"Unknown OCR engine: {Config.OCR_ENGINE}")
