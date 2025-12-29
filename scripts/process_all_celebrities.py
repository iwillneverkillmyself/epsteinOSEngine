#!/usr/bin/env python3
"""
Process all images for celebrity detection.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import get_db
from models import ImagePage, Celebrity, ImageLabel
from ocr.rekognition import RekognitionProcessor
from tqdm import tqdm

def process_all_celebrities(min_confidence=90.0):
    """Process all images that don't have celebrity detections yet."""
    
    processor = RekognitionProcessor()
    
    if not processor.enabled:
        print("❌ AWS Rekognition not configured!")
        print("Add AWS credentials to .env:")
        print("  AWS_ACCESS_KEY_ID=your_key")
        print("  AWS_SECRET_ACCESS_KEY=your_secret")
        print("  AWS_DEFAULT_REGION=us-east-1")
        return
    
    # Find images without celebrity detections
    with get_db() as db:
        # Get all image page IDs
        all_images = set(row[0] for row in db.query(ImagePage.id).all())
        
        # Get images that already have celebrity detections
        processed_images = set(
            row[0] for row in db.query(Celebrity.image_page_id).distinct().all()
        )
        
        # Find unprocessed images
        unprocessed = list(all_images - processed_images)
    
    print(f"\n{'='*80}")
    print(f"CELEBRITY DETECTION PROCESSING")
    print(f"{'='*80}\n")
    print(f"Total images: {len(all_images)}")
    print(f"Already processed: {len(processed_images)}")
    print(f"To process: {len(unprocessed)}")
    print(f"Min confidence: {min_confidence}%")
    print(f"\nThis will scan {len(unprocessed)} images for celebrities...")
    print(f"Estimated AWS cost: ${len(unprocessed) * 0.001:.2f}")
    print(f"Estimated time: {len(unprocessed) * 2 / 60:.0f} minutes\n")
    
    if len(unprocessed) == 0:
        print("✓ All images already processed!")
        return
    
    # Ask for confirmation
    response = input("Continue? (y/n): ")
    if response.lower() != 'y':
        print("Cancelled.")
        return
    
    # Process images
    total_celebrities = 0
    processed_count = 0
    errors = []
    
    for page_id in tqdm(unprocessed, desc="Processing images"):
        try:
            count = processor.process_celebrities(page_id, min_confidence)
            total_celebrities += count
            processed_count += 1
            
            if count > 0:
                tqdm.write(f"  ✓ {page_id}: Found {count} celebrities")
        
        except Exception as e:
            error_msg = f"Error processing {page_id}: {str(e)}"
            errors.append(error_msg)
            tqdm.write(f"  ✗ {error_msg}")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"PROCESSING COMPLETE")
    print(f"{'='*80}\n")
    print(f"Images processed: {processed_count}/{len(unprocessed)}")
    print(f"Celebrities found: {total_celebrities}")
    print(f"Errors: {len(errors)}")
    
    if errors:
        print(f"\nErrors encountered:")
        for error in errors[:10]:  # Show first 10 errors
            print(f"  - {error}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more")
    
    print(f"\n✓ Celebrity detection complete!")
    print(f"   View results: http://localhost:8000/celebrities")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Process all images for celebrity detection")
    parser.add_argument(
        '--min-confidence',
        type=float,
        default=90.0,
        help='Minimum confidence threshold (default: 90.0)'
    )
    
    args = parser.parse_args()
    process_all_celebrities(args.min_confidence)



