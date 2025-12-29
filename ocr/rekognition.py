"""AWS Rekognition integration for image label detection."""
import os
import io
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
from PIL import Image

# Load .env file for AWS credentials
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Check if boto3 is available
try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    logger.warning("boto3 not installed. AWS Rekognition features disabled.")


class RekognitionProcessor:
    """Processes images through AWS Rekognition for label detection."""
    
    def __init__(self):
        self._client = None
        self.enabled = self._check_enabled()
    
    def _check_enabled(self) -> bool:
        """Check if Rekognition is available and configured."""
        if not BOTO3_AVAILABLE:
            return False
        
        # Check for AWS credentials
        access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        
        if not access_key or not secret_key:
            logger.info("AWS credentials not configured. Rekognition disabled.")
            return False
        
        return True
    
    @property
    def client(self):
        """Lazy load Rekognition client."""
        if self._client is None and self.enabled:
            self._client = boto3.client(
                'rekognition',
                aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
            )
        return self._client
    
    def _resize_image_for_rekognition(self, image_path: Path, max_bytes: int = 5000000) -> bytes:
        """
        Resize image to fit within Rekognition's 5MB limit.
        Returns JPEG bytes that are under the size limit.
        """
        with open(image_path, 'rb') as f:
            original_bytes = f.read()
        
        # If already under limit, return as-is
        if len(original_bytes) <= max_bytes:
            return original_bytes
        
        logger.info(f"Resizing large image: {image_path.name} ({len(original_bytes)/1024/1024:.1f}MB)")
        
        img = Image.open(image_path)
        
        # Convert to RGB if necessary (for PNG with alpha)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        
        # Try reducing quality first
        for quality in [85, 75, 65, 55, 45]:
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality)
            if buffer.tell() <= max_bytes:
                logger.info(f"Resized to {buffer.tell()/1024/1024:.1f}MB with quality={quality}")
                return buffer.getvalue()
        
        # If still too big, reduce dimensions
        while True:
            img = img.resize((int(img.width * 0.75), int(img.height * 0.75)), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=50)
            if buffer.tell() <= max_bytes or img.width < 200:
                logger.info(f"Resized to {buffer.tell()/1024/1024:.1f}MB at {img.width}x{img.height}")
                return buffer.getvalue()
        
        return buffer.getvalue()
    
    def detect_labels(self, image_path: Path, max_labels: int = 20, 
                      min_confidence: float = 70.0) -> List[Dict]:
        """
        Detect labels (objects, scenes, concepts) in an image.
        
        Args:
            image_path: Path to image file
            max_labels: Maximum number of labels to return
            min_confidence: Minimum confidence threshold (0-100)
            
        Returns:
            List of label dictionaries with name, confidence, parents, bbox, categories
        """
        if not self.enabled:
            return []
        
        try:
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
            
            response = self.client.detect_labels(
                Image={'Bytes': image_bytes},
                MaxLabels=max_labels,
                MinConfidence=min_confidence
            )
            
            labels = []
            for label in response.get('Labels', []):
                label_data = {
                    'name': label['Name'],
                    'confidence': label['Confidence'],
                    'parents': [p['Name'] for p in label.get('Parents', [])],
                    'categories': [c.get('Name') for c in label.get('Categories', [])],
                    'instances': []
                }
                
                # Get bounding boxes for specific instances
                for instance in label.get('Instances', []):
                    bbox = instance.get('BoundingBox', {})
                    label_data['instances'].append({
                        'confidence': instance.get('Confidence', label['Confidence']),
                        'bbox': {
                            'left': bbox.get('Left', 0),
                            'top': bbox.get('Top', 0),
                            'width': bbox.get('Width', 0),
                            'height': bbox.get('Height', 0)
                        }
                    })
                
                labels.append(label_data)
            
            return labels
            
        except NoCredentialsError:
            logger.error("AWS credentials not found")
            return []
        except ClientError as e:
            logger.error(f"Rekognition API error: {e}")
            return []
        except Exception as e:
            logger.error(f"Error detecting labels for {image_path}: {e}")
            return []
    
    def detect_faces(self, image_path: Path) -> List[Dict]:
        """Detect faces in an image with attributes."""
        if not self.enabled:
            return []
        
        try:
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
            
            response = self.client.detect_faces(
                Image={'Bytes': image_bytes},
                Attributes=['ALL']
            )
            
            faces = []
            for face in response.get('FaceDetails', []):
                bbox = face.get('BoundingBox', {})
                faces.append({
                    'confidence': face.get('Confidence', 0),
                    'bbox': {
                        'left': bbox.get('Left', 0),
                        'top': bbox.get('Top', 0),
                        'width': bbox.get('Width', 0),
                        'height': bbox.get('Height', 0)
                    },
                    'age_range': face.get('AgeRange'),
                    'gender': face.get('Gender'),
                    'emotions': face.get('Emotions', []),
                    'smile': face.get('Smile'),
                    'eyeglasses': face.get('Eyeglasses'),
                    'sunglasses': face.get('Sunglasses'),
                    'beard': face.get('Beard'),
                    'mustache': face.get('Mustache')
                })
            
            return faces
            
        except Exception as e:
            logger.error(f"Error detecting faces for {image_path}: {e}")
            return []
    
    def recognize_celebrities(self, image_path: Path) -> List[Dict]:
        """Recognize celebrities in an image. Auto-resizes large images."""
        if not self.enabled:
            return []
        
        try:
            # Auto-resize if needed to fit within 5MB limit
            image_bytes = self._resize_image_for_rekognition(Path(image_path))
            
            response = self.client.recognize_celebrities(
                Image={'Bytes': image_bytes}
            )
            
            celebrities = []
            for celeb in response.get('CelebrityFaces', []):
                bbox = celeb.get('Face', {}).get('BoundingBox', {})
                celebrities.append({
                    'name': celeb.get('Name'),
                    'confidence': celeb.get('MatchConfidence', 0),
                    'urls': celeb.get('Urls', []),
                    'bbox': {
                        'left': bbox.get('Left', 0),
                        'top': bbox.get('Top', 0),
                        'width': bbox.get('Width', 0),
                        'height': bbox.get('Height', 0)
                    }
                })
            
            return celebrities
            
        except Exception as e:
            logger.error(f"Error recognizing celebrities for {image_path}: {e}")
            return []
    
    def process_image_page(self, page_id: str) -> int:
        """
        Process an image page through Rekognition and store labels.
        
        Returns:
            Number of labels stored
        """
        if not self.enabled:
            return 0
        
        from database import get_db
        from models import ImagePage, ImageLabel
        
        with get_db() as db:
            page = db.query(ImagePage).filter(ImagePage.id == page_id).first()
            if not page:
                logger.error(f"Image page {page_id} not found")
                return 0
            
            # Check if already processed (has labels)
            existing = db.query(ImageLabel).filter(
                ImageLabel.image_page_id == page_id
            ).first()
            if existing:
                logger.debug(f"Page {page_id} already has labels")
                return 0
            
            image_path = Path(page.image_path)
            if not image_path.exists():
                logger.error(f"Image not found: {image_path}")
                return 0
            
            # Detect labels
            logger.info(f"Detecting labels for {page_id}")
            labels = self.detect_labels(image_path, max_labels=20, min_confidence=70.0)
            
            # Store labels
            count = 0
            for label_data in labels:
                # Store main label
                label = ImageLabel(
                    id=str(uuid.uuid4()),
                    image_page_id=page_id,
                    document_id=page.document_id,
                    label_name=label_data['name'],
                    label_name_lower=label_data['name'].lower(),
                    confidence=label_data['confidence'],
                    parent_labels=label_data['parents'],
                    categories=label_data['categories'],
                    has_bbox=False
                )
                db.add(label)
                count += 1
                
                # Store instances with bounding boxes
                for instance in label_data.get('instances', []):
                    bbox = instance['bbox']
                    instance_label = ImageLabel(
                        id=str(uuid.uuid4()),
                        image_page_id=page_id,
                        document_id=page.document_id,
                        label_name=label_data['name'],
                        label_name_lower=label_data['name'].lower(),
                        confidence=instance['confidence'],
                        parent_labels=label_data['parents'],
                        categories=label_data['categories'],
                        has_bbox=True,
                        bbox_left=bbox['left'],
                        bbox_top=bbox['top'],
                        bbox_width=bbox['width'],
                        bbox_height=bbox['height']
                    )
                    db.add(instance_label)
                    count += 1
            
            db.commit()
            logger.info(f"Stored {count} labels for {page_id}")
            return count
    
    def process_celebrities(self, page_id: str, min_confidence: float = 90.0) -> int:
        """
        Process an image page for celebrity detection and store results.
        
        Args:
            page_id: ID of the image page to process
            min_confidence: Minimum confidence threshold (default 90%)
            
        Returns:
            Number of celebrities detected and stored
        """
        if not self.enabled:
            return 0
        
        from database import get_db
        from models import ImagePage, Celebrity
        
        with get_db() as db:
            page = db.query(ImagePage).filter(ImagePage.id == page_id).first()
            if not page:
                logger.error(f"Image page {page_id} not found")
                return 0
            
            # Check if already processed for celebrities
            existing = db.query(Celebrity).filter(
                Celebrity.image_page_id == page_id
            ).first()
            if existing:
                logger.debug(f"Page {page_id} already processed for celebrities")
                return 0
            
            image_path = Path(page.image_path)
            if not image_path.exists():
                logger.error(f"Image not found: {image_path}")
                return 0
            
            # Recognize celebrities
            logger.info(f"Recognizing celebrities for {page_id}")
            celebrities = self.recognize_celebrities(image_path)
            
            # Store celebrities above confidence threshold
            count = 0
            for celeb_data in celebrities:
                if celeb_data['confidence'] < min_confidence:
                    continue
                    
                bbox = celeb_data.get('bbox', {})
                celebrity = Celebrity(
                    id=str(uuid.uuid4()),
                    image_page_id=page_id,
                    document_id=page.document_id,
                    page_number=page.page_number,
                    name=celeb_data['name'],
                    name_lower=celeb_data['name'].lower(),
                    confidence=celeb_data['confidence'],
                    urls=celeb_data.get('urls', []),
                    bbox_left=bbox.get('left', 0),
                    bbox_top=bbox.get('top', 0),
                    bbox_width=bbox.get('width', 0),
                    bbox_height=bbox.get('height', 0)
                )
                db.add(celebrity)
                count += 1
                logger.info(f"Found celebrity: {celeb_data['name']} ({celeb_data['confidence']:.1f}%)")
            
            db.commit()
            
            if count > 0:
                logger.info(f"Stored {count} celebrities for {page_id}")
            return count
    
    def process_all_for_celebrities(self, limit: int = 1000, min_confidence: float = 90.0) -> Dict:
        """
        Process all unprocessed image pages for celebrity detection.
        
        Returns:
            Dict with processing statistics
        """
        if not self.enabled:
            return {'error': 'Rekognition not enabled', 'processed': 0, 'celebrities_found': 0}
        
        from database import get_db
        from models import ImagePage, Celebrity
        
        with get_db() as db:
            # Find pages not yet processed for celebrities
            processed_page_ids = db.query(Celebrity.image_page_id).distinct().subquery()
            
            unprocessed = db.query(ImagePage.id).filter(
                ~ImagePage.id.in_(processed_page_ids)
            ).limit(limit).all()
            
            page_ids = [row[0] for row in unprocessed]
        
        total_celebrities = 0
        processed = 0
        
        for page_id in page_ids:
            celebrities_found = self.process_celebrities(page_id, min_confidence)
            total_celebrities += celebrities_found
            processed += 1
        
        return {
            'processed': processed,
            'celebrities_found': total_celebrities,
            'remaining': len(page_ids) - processed if len(page_ids) > processed else 0
        }

