"""AWS Textract OCR engine for high-accuracy text extraction."""
import os
import logging
from pathlib import Path
from typing import Dict, List, Optional

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
    logger.warning("boto3 not installed. AWS Textract features disabled.")


class TextractEngine:
    """
    AWS Textract-based OCR engine.
    
    Features:
    - High-accuracy text detection
    - Word and line-level bounding boxes
    - Confidence scores per word
    - Handwriting recognition
    - Table and form extraction (optional)
    
    Uses the same AWS credentials as Rekognition.
    """
    
    def __init__(self):
        self._client = None
        self.enabled = self._check_enabled()
    
    def _check_enabled(self) -> bool:
        """Check if Textract is available and configured."""
        if not BOTO3_AVAILABLE:
            logger.warning("boto3 not available - Textract disabled")
            return False
        
        # Check for AWS credentials (either explicit or IAM role)
        access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        
        # If explicit credentials are provided, use them
        if access_key and secret_key:
            return True
        
        # Otherwise, try to use IAM role credentials (for ECS Fargate)
        # boto3 will automatically use the task role if available
        # We need to actually try to create a client to test if credentials work
        try:
            import boto3
            from botocore.exceptions import NoCredentialsError, ClientError
            
            # Try to create a client and make a lightweight API call to verify credentials
            # Use STS get-caller-identity as it's a simple, fast call
            sts_client = boto3.client('sts', region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'))
            sts_client.get_caller_identity()
            logger.info("Using IAM role credentials for Textract")
            return True
        except NoCredentialsError:
            logger.warning("AWS credentials not configured - Textract disabled")
            return False
        except (ClientError, Exception) as e:
            # If we get a permissions error, credentials exist but may not have permissions
            # Still enable Textract - the actual API call will show the real error
            logger.info(f"Detected IAM role credentials (may have permission issues): {e}")
            return True
    
    @property
    def client(self):
        """Lazy load Textract client."""
        if self._client is None and self.enabled:
            # Use explicit credentials if provided, otherwise use IAM role (for ECS)
            access_key = os.environ.get('AWS_ACCESS_KEY_ID')
            secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
            
            if access_key and secret_key:
                # Explicit credentials provided
                self._client = boto3.client(
                    'textract',
                    aws_access_key_id=access_key,
                    aws_secret_access_key=secret_key,
                    region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
                )
            else:
                # Use IAM role credentials (boto3 will automatically use task role)
                self._client = boto3.client(
                    'textract',
                    region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
                )
        return self._client
    
    def extract_text(self, image_path: Path) -> Dict:
        """
        Extract text from image using AWS Textract.
        
        Args:
            image_path: Path to image file
            
        Returns:
            {
                'text': str,
                'word_boxes': List[Dict],  # [{text, x, y, width, height, confidence}]
                'confidence': float,
                'engine': str,
                'metadata': dict
            }
        """
        if not self.enabled:
            logger.error("Textract not enabled - missing credentials or boto3")
            return {
                'text': '',
                'word_boxes': [],
                'confidence': 0.0,
                'engine': 'textract',
                'metadata': {'error': 'Textract not configured'}
            }
        
        try:
            # Read image bytes
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
            
            # Call Textract DetectDocumentText API
            logger.info(f"Calling Textract for {image_path.name}")
            response = self.client.detect_document_text(
                Document={'Bytes': image_bytes}
            )
            
            # Parse response
            return self._parse_response(response, image_path)
            
        except NoCredentialsError:
            logger.error("AWS credentials not found")
            return self._error_result("AWS credentials not found")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"Textract API error ({error_code}): {error_msg}")
            return self._error_result(f"Textract API error: {error_msg}")
        except Exception as e:
            logger.exception(f"Error in Textract extraction for {image_path}: {e}")
            return self._error_result(str(e))
    
    def _parse_response(self, response: Dict, image_path: Path) -> Dict:
        """Parse Textract response into standard format."""
        blocks = response.get('Blocks', [])
        
        # Separate lines and words
        lines = []
        word_boxes = []
        confidences = []
        
        # Get image dimensions for coordinate conversion
        # Textract returns normalized coordinates (0-1)
        # We'll store them as-is and let the frontend handle scaling
        
        for block in blocks:
            block_type = block.get('BlockType')
            
            if block_type == 'LINE':
                text = block.get('Text', '')
                if text:
                    lines.append(text)
            
            elif block_type == 'WORD':
                text = block.get('Text', '')
                confidence = block.get('Confidence', 0.0) / 100.0  # Convert to 0-1
                
                if text:
                    # Get bounding box (normalized 0-1 coordinates)
                    bbox = block.get('Geometry', {}).get('BoundingBox', {})
                    
                    word_boxes.append({
                        'text': text,
                        'x': bbox.get('Left', 0),
                        'y': bbox.get('Top', 0),
                        'width': bbox.get('Width', 0),
                        'height': bbox.get('Height', 0),
                        'confidence': confidence,
                        # Store polygon for precise highlighting
                        'polygon': block.get('Geometry', {}).get('Polygon', [])
                    })
                    
                    confidences.append(confidence)
        
        # Combine lines into full text
        full_text = ' '.join(lines)
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        
        logger.info(f"Textract extracted {len(word_boxes)} words, "
                   f"{len(lines)} lines, confidence: {avg_confidence:.2f}")
        
        return {
            'text': full_text,
            'word_boxes': word_boxes,
            'confidence': avg_confidence,
            'engine': 'textract',
            'metadata': {
                'word_count': len(word_boxes),
                'line_count': len(lines),
                'source': image_path.name
            }
        }
    
    def _error_result(self, error_msg: str) -> Dict:
        """Return error result in standard format."""
        return {
            'text': '',
            'word_boxes': [],
            'confidence': 0.0,
            'engine': 'textract',
            'metadata': {'error': error_msg}
        }
    
    def analyze_document(self, image_path: Path, feature_types: List[str] = None) -> Dict:
        """
        Analyze document with additional features (tables, forms).
        
        Args:
            image_path: Path to image file
            feature_types: List of features - 'TABLES', 'FORMS', 'SIGNATURES'
            
        Returns:
            Extended analysis result
        """
        if not self.enabled:
            return self._error_result("Textract not configured")
        
        if feature_types is None:
            feature_types = ['TABLES', 'FORMS']
        
        try:
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
            
            response = self.client.analyze_document(
                Document={'Bytes': image_bytes},
                FeatureTypes=feature_types
            )
            
            # Parse basic text
            result = self._parse_response(response, image_path)
            
            # Add table data if present
            tables = self._extract_tables(response.get('Blocks', []))
            if tables:
                result['metadata']['tables'] = tables
            
            # Add form data if present
            forms = self._extract_forms(response.get('Blocks', []))
            if forms:
                result['metadata']['forms'] = forms
            
            return result
            
        except Exception as e:
            logger.exception(f"Error in Textract analysis: {e}")
            return self._error_result(str(e))
    
    def _extract_tables(self, blocks: List[Dict]) -> List[Dict]:
        """Extract table data from Textract blocks."""
        tables = []
        # Table extraction is complex - simplified version
        table_blocks = [b for b in blocks if b.get('BlockType') == 'TABLE']
        for table in table_blocks:
            tables.append({
                'id': table.get('Id'),
                'confidence': table.get('Confidence', 0)
            })
        return tables
    
    def _extract_forms(self, blocks: List[Dict]) -> List[Dict]:
        """Extract form key-value pairs from Textract blocks."""
        forms = []
        # Form extraction is complex - simplified version
        kv_blocks = [b for b in blocks if b.get('BlockType') == 'KEY_VALUE_SET']
        for kv in kv_blocks:
            if kv.get('EntityTypes') == ['KEY']:
                forms.append({
                    'id': kv.get('Id'),
                    'confidence': kv.get('Confidence', 0)
                })
        return forms


