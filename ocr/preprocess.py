"""Image preprocessing utilities to improve OCR on low-quality scans.

Best-practice preprocessing pipeline for maximum OCR accuracy:
- Deskewing (rotation correction)
- Denoising
- Contrast normalization (CLAHE)
- Sharpening
- Adaptive thresholding
- Multi-scale variants

Bounding box coordinates are preserved/transformed correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional
import math

import cv2
import numpy as np


@dataclass(frozen=True)
class OCRVariant:
    """Represents a preprocessed image variant for OCR."""
    name: str
    image: np.ndarray  # HxWxC or HxW
    scale: float = 1.0  # If variant was upscaled, boxes should be divided by this.
    rotation_angle: float = 0.0  # Degrees rotated (for coordinate transform)


@dataclass
class DeskewResult:
    """Result of deskewing operation."""
    image: np.ndarray
    angle: float  # Rotation angle applied (degrees)
    transform_matrix: Optional[np.ndarray] = None


def _to_gray(img: np.ndarray) -> np.ndarray:
    """Convert to grayscale if needed."""
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def _ensure_rgb(img: np.ndarray) -> np.ndarray:
    """Ensure image is RGB format."""
    if img.ndim == 3 and img.shape[2] == 3:
        return img
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    return img


def _clahe(gray: np.ndarray, clip_limit: float = 2.0, tile_size: int = 8) -> np.ndarray:
    """Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)."""
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
    return clahe.apply(gray)


def _sharpen(img: np.ndarray) -> np.ndarray:
    """Apply unsharp masking for text sharpening."""
    if img.ndim == 2:
        # Grayscale sharpening
        blurred = cv2.GaussianBlur(img, (0, 0), 3)
        sharpened = cv2.addWeighted(img, 1.5, blurred, -0.5, 0)
        return sharpened
    else:
        # RGB sharpening
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        return cv2.filter2D(img, -1, kernel)


def _denoise(gray: np.ndarray, h: int = 10) -> np.ndarray:
    """Apply non-local means denoising."""
    return cv2.fastNlMeansDenoising(gray, h=h)


def _denoise_color(rgb: np.ndarray, h: int = 10) -> np.ndarray:
    """Apply non-local means denoising to color image."""
    return cv2.fastNlMeansDenoisingColored(rgb, h=h, hColor=h)


def _adaptive_thresh(gray: np.ndarray, block_size: int = 35, c: int = 11) -> np.ndarray:
    """Apply adaptive thresholding for binarization."""
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        block_size,
        c,
    )


def _otsu_thresh(gray: np.ndarray) -> np.ndarray:
    """Apply Otsu's thresholding."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def detect_skew_angle(gray: np.ndarray, max_angle: float = 15.0) -> float:
    """
    Detect skew angle of text in image using projection profile or Hough lines.
    
    Returns angle in degrees to rotate image to deskew it.
    """
    # Method 1: Use Hough lines to detect dominant text line angle
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=100,
        minLineLength=gray.shape[1] // 8,
        maxLineGap=10
    )
    
    if lines is not None and len(lines) > 0:
        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if x2 - x1 != 0:
                angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
                # Only consider near-horizontal lines (text lines)
                if abs(angle) < max_angle:
                    angles.append(angle)
        
        if angles:
            # Use median to be robust to outliers
            return float(np.median(angles))
    
    # Method 2: Projection profile analysis (fallback)
    # Try different angles and find the one with maximum variance
    best_angle = 0.0
    best_var = 0.0
    
    for angle in np.linspace(-max_angle, max_angle, 31):
        rotated = rotate_image(gray, angle)
        # Calculate horizontal projection profile
        profile = np.sum(rotated, axis=1)
        var = np.var(profile)
        if var > best_var:
            best_var = var
            best_angle = angle
    
    return best_angle


def rotate_image(img: np.ndarray, angle: float, 
                 border_color: Tuple[int, ...] = (255, 255, 255)) -> np.ndarray:
    """
    Rotate image by given angle (degrees) around center.
    Expands canvas to fit rotated image without cropping.
    """
    if abs(angle) < 0.01:
        return img
    
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    
    # Get rotation matrix
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    
    # Calculate new bounding box size
    cos_a = abs(M[0, 0])
    sin_a = abs(M[0, 1])
    new_w = int(h * sin_a + w * cos_a)
    new_h = int(h * cos_a + w * sin_a)
    
    # Adjust the rotation matrix
    M[0, 2] += (new_w - w) / 2
    M[1, 2] += (new_h - h) / 2
    
    # Perform rotation
    if img.ndim == 2:
        border = border_color[0] if isinstance(border_color, tuple) else border_color
    else:
        border = border_color
    
    rotated = cv2.warpAffine(
        img, M, (new_w, new_h),
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border
    )
    
    return rotated


def deskew_image(img: np.ndarray, max_angle: float = 15.0) -> DeskewResult:
    """
    Detect and correct skew in scanned document.
    
    Returns deskewed image and the angle that was corrected.
    """
    gray = _to_gray(img)
    angle = detect_skew_angle(gray, max_angle)
    
    if abs(angle) < 0.1:
        return DeskewResult(image=img, angle=0.0)
    
    rotated = rotate_image(img, -angle)  # Negative to correct the skew
    return DeskewResult(image=rotated, angle=-angle)


def enhance_for_ocr(rgb_img: np.ndarray, denoise: bool = True) -> np.ndarray:
    """
    Apply best-practice preprocessing for OCR accuracy.
    
    Pipeline:
    1. Denoise
    2. Convert to grayscale
    3. CLAHE contrast normalization
    4. Sharpen
    5. Convert back to RGB for OCR engines that expect it
    """
    rgb = _ensure_rgb(rgb_img)
    
    # Denoise color image
    if denoise:
        rgb = _denoise_color(rgb, h=8)
    
    # Convert to grayscale for processing
    gray = _to_gray(rgb)
    
    # CLAHE for contrast normalization
    gray = _clahe(gray, clip_limit=2.0, tile_size=8)
    
    # Sharpen
    gray = _sharpen(gray)
    
    # Convert back to RGB
    return _ensure_rgb(gray)


def build_ocr_variants(rgb_img: np.ndarray, 
                       scales: List[float],
                       deskew: bool = True,
                       max_variants: int = 8) -> List[OCRVariant]:
    """
    Build a set of OCR variants for aggressive text recovery.
    
    Creates multiple preprocessing variants and scales to maximize
    text detection on difficult/noisy scans.
    
    Args:
        rgb_img: Input RGB image
        scales: List of scale factors (e.g., [1.0, 2.0])
        deskew: Whether to apply deskewing
        max_variants: Maximum number of variants to generate
        
    Returns:
        List of OCRVariant objects ready for OCR
    """
    variants: List[OCRVariant] = []
    
    rgb_img = _ensure_rgb(rgb_img)
    rotation_angle = 0.0
    
    # Apply deskewing if enabled
    if deskew:
        result = deskew_image(rgb_img, max_angle=10.0)
        if abs(result.angle) > 0.1:
            rgb_img = result.image
            rotation_angle = result.angle
    
    gray = _to_gray(rgb_img)
    
    # Build preprocessing variants
    denoised = _denoise(gray, h=10)
    clahe_img = _clahe(denoised, clip_limit=2.0)
    sharp = _sharpen(clahe_img)
    adaptive_bin = _adaptive_thresh(clahe_img, block_size=35, c=11)
    otsu_bin = _otsu_thresh(clahe_img)
    
    # Enhanced color variant
    enhanced_rgb = enhance_for_ocr(rgb_img, denoise=True)
    
    base_variants = [
        ("original", rgb_img),
        ("enhanced", enhanced_rgb),
        ("clahe", _ensure_rgb(clahe_img)),
        ("sharp", _ensure_rgb(sharp)),
        ("adaptive_bin", _ensure_rgb(adaptive_bin)),
    ]
    
    # Add variants at scale 1.0
    for name, img in base_variants:
        if len(variants) >= max_variants:
            break
        variants.append(OCRVariant(
            name=name, 
            image=img, 
            scale=1.0,
            rotation_angle=rotation_angle
        ))
    
    # Add scaled variants for small text
    h, w = rgb_img.shape[:2]
    for s in scales:
        if s <= 1.0:
            continue
        for name, img in base_variants[:3]:  # Only scale top 3 variants
            if len(variants) >= max_variants:
                break
            scaled = cv2.resize(
                img, 
                (int(w * s), int(h * s)), 
                interpolation=cv2.INTER_CUBIC
            )
            variants.append(OCRVariant(
                name=f"{name}_x{int(s)}",
                image=scaled,
                scale=s,
                rotation_angle=rotation_angle
            ))
    
    return variants


def transform_bbox_for_deskew(bbox: Tuple[float, float, float, float],
                               angle: float,
                               original_size: Tuple[int, int],
                               new_size: Tuple[int, int]) -> Tuple[float, float, float, float]:
    """
    Transform bounding box coordinates from deskewed image back to original.
    
    Args:
        bbox: (x, y, width, height) in deskewed image
        angle: Rotation angle that was applied (degrees)
        original_size: (width, height) of original image
        new_size: (width, height) of deskewed image
        
    Returns:
        (x, y, width, height) in original image coordinates
    """
    if abs(angle) < 0.01:
        return bbox
    
    x, y, w, h = bbox
    orig_w, orig_h = original_size
    new_w, new_h = new_size
    
    # Calculate center points in deskewed image
    cx = x + w / 2
    cy = y + h / 2
    
    # Translate to center of deskewed image
    cx -= new_w / 2
    cy -= new_h / 2
    
    # Rotate back (negative angle)
    angle_rad = math.radians(-angle)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    
    new_cx = cx * cos_a - cy * sin_a
    new_cy = cx * sin_a + cy * cos_a
    
    # Translate to original image center
    new_cx += orig_w / 2
    new_cy += orig_h / 2
    
    # Return bbox (note: width/height don't change much for small angles)
    return (new_cx - w / 2, new_cy - h / 2, w, h)
