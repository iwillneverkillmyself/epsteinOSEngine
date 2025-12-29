#!/usr/bin/env python3
"""
Diagnostic script to check celebrity detection status.
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import get_db
from models import ImagePage, Celebrity, ImageLabel
from sqlalchemy import func

def diagnose():
    """Check what's in the database."""
    
    with get_db() as db:
        # Count total images
        total_images = db.query(ImagePage).count()
        print(f"\n{'='*80}")
        print(f"DATABASE DIAGNOSTIC")
        print(f"{'='*80}\n")
        print(f"Total image pages: {total_images}")
        
        # Count images with celebrities
        images_with_celebrities = db.query(Celebrity.image_page_id).distinct().count()
        print(f"Images with celebrities detected: {images_with_celebrities}")
        
        # Count total celebrity detections
        total_celebrities = db.query(Celebrity).count()
        print(f"Total celebrity detections: {total_celebrities}")
        
        # Count images with labels (Rekognition processed)
        images_with_labels = db.query(ImageLabel.image_page_id).distinct().count()
        print(f"Images processed by Rekognition (labels): {images_with_labels}")
        
        # Find images that have labels but NO celebrities
        images_with_labels_ids = set(
            row[0] for row in db.query(ImageLabel.image_page_id).distinct().all()
        )
        images_with_celebrities_ids = set(
            row[0] for row in db.query(Celebrity.image_page_id).distinct().all()
        )
        
        processed_without_celebrities = images_with_labels_ids - images_with_celebrities_ids
        print(f"\nImages processed by Rekognition but NO celebrities found: {len(processed_without_celebrities)}")
        
        # Find images that haven't been processed by Rekognition at all
        all_image_ids = set(row[0] for row in db.query(ImagePage.id).all())
        not_processed = all_image_ids - images_with_labels_ids
        print(f"Images NOT yet processed by Rekognition: {len(not_processed)}")
        
        # List celebrities found
        print(f"\n{'='*80}")
        print(f"CELEBRITIES DETECTED")
        print(f"{'='*80}\n")
        
        celebrity_counts = db.query(
            Celebrity.name,
            func.count(Celebrity.id).label('count'),
            func.avg(Celebrity.confidence).label('avg_conf')
        ).group_by(Celebrity.name).order_by(func.count(Celebrity.id).desc()).all()
        
        if celebrity_counts:
            for name, count, avg_conf in celebrity_counts:
                print(f"  {name}: {count} appearances (avg confidence: {avg_conf:.1f}%)")
        else:
            print("  No celebrities detected yet!")
        
        # Check if celebrity detection has been run
        print(f"\n{'='*80}")
        print(f"RECOMMENDATIONS")
        print(f"{'='*80}\n")
        
        if len(not_processed) > 0:
            print(f"⚠️  {len(not_processed)} images have NOT been processed by Rekognition yet.")
            print(f"   Run: curl -X POST 'http://localhost:8000/process/celebrities?limit=1000'")
            print(f"   Or: python -c 'from ocr.rekognition import RekognitionProcessor; rp = RekognitionProcessor(); [rp.process_celebrities(pid) for pid in {list(not_processed)[:10]}]'")
        
        if len(processed_without_celebrities) > 0:
            print(f"\n✓  {len(processed_without_celebrities)} images were processed but no celebrities found.")
            print(f"   This is normal - not all images contain celebrities.")
        
        if images_with_celebrities > 0:
            print(f"\n✓  {images_with_celebrities} images have celebrity detections!")
            print(f"   API should show these at: http://localhost:8000/celebrities")
        
        # Sample some image pages to see their status
        print(f"\n{'='*80}")
        print(f"SAMPLE IMAGE STATUS (first 10)")
        print(f"{'='*80}\n")
        
        sample_images = db.query(ImagePage).limit(10).all()
        for img in sample_images:
            has_labels = db.query(ImageLabel).filter(ImageLabel.image_page_id == img.id).count() > 0
            has_celebs = db.query(Celebrity).filter(Celebrity.image_page_id == img.id).count() > 0
            celeb_count = db.query(Celebrity).filter(Celebrity.image_page_id == img.id).count()
            
            status = []
            if has_labels:
                status.append("✓ Rekognition processed")
            else:
                status.append("✗ Not processed")
            
            if has_celebs:
                status.append(f"✓ {celeb_count} celebrities")
            else:
                status.append("✗ No celebrities")
            
            print(f"  {img.id}: {' | '.join(status)}")
        
        print(f"\n{'='*80}\n")


if __name__ == "__main__":
    diagnose()



