# Epstein Files Image Analysis (AWS Rekognition)

Node.js TypeScript script that analyzes thumbnails from the Epstein files using AWS Rekognition.

## Features

- **DetectLabels**: Identifies objects and scenes in images
- **DetectFaces**: Detects faces with full attributes (age, gender, emotions, glasses, etc.)
- **RecognizeCelebrities**: Matches faces against Amazon's celebrity database

## Prerequisites

1. **Node.js 18+** installed
2. **AWS credentials** configured via:
   - Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
   - AWS credentials file (`~/.aws/credentials`)
   - IAM role (if running on AWS infrastructure)

## Setup

```bash
cd image-analysis
npm install
```

## Usage

```bash
# Run analysis
npm run analyze

# Or with custom options
SOURCE_ENDPOINT=https://epstein-files.rhys-669.workers.dev \
CONCURRENCY_LIMIT=5 \
OUTPUT_DIR=./my-results \
AWS_REGION=us-east-1 \
npm run analyze
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SOURCE_ENDPOINT` | `https://epstein-files.rhys-669.workers.dev` | Base URL for file discovery and thumbnails |
| `CONCURRENCY_LIMIT` | `3` | Max concurrent Rekognition API calls |
| `OUTPUT_DIR` | `./results` | Directory for output JSON files |
| `AWS_REGION` | `us-east-1` | AWS region for Rekognition |

## Output

Results are written to the output directory:

- **`{filename}.analysis.json`** - Individual analysis per file
- **`summary.json`** - Aggregated summary with celebrity matches

### Example Output

```json
{
  "filename": "document-001.pdf",
  "thumbnailUrl": "https://epstein-files.rhys-669.workers.dev/thumbnails/document-001.jpg",
  "analyzedAt": "2024-01-15T12:00:00.000Z",
  "labels": [
    { "name": "Person", "confidence": 99.5 },
    { "name": "Document", "confidence": 95.2 }
  ],
  "faces": [
    {
      "boundingBox": { "left": 0.1, "top": 0.2, "width": 0.3, "height": 0.4 },
      "ageRange": { "low": 30, "high": 40 },
      "gender": { "value": "Male", "confidence": 98.5 },
      "emotions": [
        { "type": "CALM", "confidence": 85.0 }
      ]
    }
  ],
  "celebrities": [
    {
      "name": "Famous Person",
      "matchConfidence": 99.0,
      "urls": ["https://www.imdb.com/name/..."],
      "boundingBox": { "left": 0.1, "top": 0.2, "width": 0.3, "height": 0.4 }
    }
  ],
  "errors": []
}
```

## API Costs

AWS Rekognition pricing (as of 2024):
- **DetectLabels**: ~$0.001 per image
- **DetectFaces**: ~$0.001 per image
- **RecognizeCelebrities**: ~$0.001 per image

Total: ~$0.003 per thumbnail analyzed

## Error Handling

The script gracefully handles:
- Missing thumbnails (404 responses)
- Non-image responses
- Rekognition API failures
- Network timeouts

Errors are logged but don't stop the analysis of other files.



