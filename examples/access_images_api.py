#!/usr/bin/env python3
"""
Example: Access images via API

This script demonstrates how to:
1. List all available images
2. Download specific images
3. Get thumbnails
4. Search for images by content
"""

import requests
import json
from pathlib import Path


# API base URL (change if your API is running elsewhere)
API_BASE = "http://localhost:8000"


def list_all_images(limit=10):
    """List all available images."""
    print(f"\n{'='*80}")
    print("LISTING ALL IMAGES")
    print('='*80)
    
    response = requests.get(f"{API_BASE}/images", params={"limit": limit})
    
    if response.status_code == 200:
        data = response.json()
        print(f"\nTotal images: {data['total']}")
        print(f"Showing: {data['count']} (offset: {data['offset']})\n")
        
        for img in data['images']:
            print(f"Page ID: {img['page_id']}")
            print(f"  Document: {img['file_name']}")
            print(f"  Page: {img['page_number']}")
            print(f"  Size: {img['width']}x{img['height']}")
            print(f"  OCR Processed: {img['ocr_processed']}")
            print(f"  Image URL: {img['image_url']}")
            print(f"  Thumbnail URL: {img['thumbnail_url']}")
            print()
        
        return data['images']
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return []


def download_image(page_id, output_dir="./downloaded_images"):
    """Download a specific image."""
    print(f"\n{'='*80}")
    print(f"DOWNLOADING IMAGE: {page_id}")
    print('='*80)
    
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    response = requests.get(f"{API_BASE}/images/{page_id}")
    
    if response.status_code == 200:
        file_path = output_path / f"{page_id}.png"
        with open(file_path, 'wb') as f:
            f.write(response.content)
        print(f"✓ Saved to: {file_path}")
        print(f"  Size: {len(response.content) / 1024:.1f} KB")
        return file_path
    else:
        print(f"✗ Error: {response.status_code}")
        return None


def download_thumbnail(page_id, width=300, output_dir="./downloaded_images"):
    """Download a thumbnail of an image."""
    print(f"\n{'='*80}")
    print(f"DOWNLOADING THUMBNAIL: {page_id}")
    print('='*80)
    
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    response = requests.get(
        f"{API_BASE}/thumbnails/{page_id}",
        params={"width": width}
    )
    
    if response.status_code == 200:
        file_path = output_path / f"{page_id}_thumb.png"
        with open(file_path, 'wb') as f:
            f.write(response.content)
        print(f"✓ Saved to: {file_path}")
        print(f"  Size: {len(response.content) / 1024:.1f} KB")
        return file_path
    else:
        print(f"✗ Error: {response.status_code}")
        return None


def search_and_download(query, limit=5):
    """Search for text and download related images."""
    print(f"\n{'='*80}")
    print(f"SEARCHING FOR: {query}")
    print('='*80)
    
    # Search for text
    response = requests.get(
        f"{API_BASE}/search",
        params={
            "q": query,
            "search_type": "keyword",
            "limit": limit
        }
    )
    
    if response.status_code != 200:
        print(f"✗ Search failed: {response.status_code}")
        return
    
    data = response.json()
    print(f"\nFound {data['count']} results\n")
    
    for result in data['results']:
        print(f"Document: {result['document_id']}")
        print(f"Page: {result['page_number']}")
        print(f"Confidence: {result.get('confidence', 0):.2f}")
        print(f"Snippet: {result['snippet'][:100]}...")
        
        # Download the image for this result
        if result.get('image_path'):
            # Extract page_id from the result
            # The page_id is typically document_id + _page_0001 format
            page_id = f"{result['document_id']}_page_{result['page_number']:04d}"
            download_thumbnail(page_id, width=400, output_dir="./search_results")
        
        print()


def get_document_pages(document_id):
    """Get all pages for a specific document."""
    print(f"\n{'='*80}")
    print(f"GETTING PAGES FOR DOCUMENT: {document_id}")
    print('='*80)
    
    response = requests.get(f"{API_BASE}/documents/{document_id}/pages")
    
    if response.status_code == 200:
        data = response.json()
        print(f"\nDocument ID: {data['document_id']}")
        print(f"Total Pages: {data['page_count']}\n")
        
        for page in data['pages']:
            print(f"Page {page['page_number']}:")
            print(f"  Page ID: {page['page_id']}")
            print(f"  Size: {page['width']}x{page['height']}")
            print(f"  Image URL: {page['image_url']}")
            print()
        
        return data['pages']
    else:
        print(f"✗ Error: {response.status_code}")
        return []


def main():
    """Main demo."""
    print("\n" + "="*80)
    print("IMAGE API ACCESS EXAMPLES")
    print("="*80)
    print("\nMake sure the API server is running:")
    print("  python main.py")
    print("  # or")
    print("  uvicorn api.main:app --reload")
    print("\n" + "="*80)
    
    # Example 1: List images
    images = list_all_images(limit=5)
    
    if not images:
        print("\n⚠️  No images found. Run the ingestion pipeline first:")
        print("  python pipeline.py")
        print("  # or")
        print("  python scripts/ingest_doj_files.py")
        return
    
    # Example 2: Download first image
    if images:
        first_image = images[0]
        download_image(first_image['page_id'])
        download_thumbnail(first_image['page_id'], width=300)
    
    # Example 3: Search and download
    search_and_download("flight", limit=3)
    
    # Example 4: Get all pages of a document
    if images:
        doc_id = images[0]['document_id']
        get_document_pages(doc_id)
    
    print("\n" + "="*80)
    print("EXAMPLES COMPLETE")
    print("="*80)
    print("\nCheck these directories:")
    print("  ./downloaded_images/  - Full images and thumbnails")
    print("  ./search_results/     - Images from search results")
    print()


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.ConnectionError:
        print("\n❌ Error: Could not connect to API server")
        print("Make sure the API is running:")
        print("  python main.py")
        print("  # or")
        print("  uvicorn api.main:app --reload")
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")



