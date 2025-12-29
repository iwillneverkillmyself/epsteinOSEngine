# Frontend API Key Integration Guide

## Your API Key
```
LAf4zsG1Zg3ePbFqkWQvNemTRy5S0c2d
```

## API Base URL
```
http://epsteingptengine-alb-1564453343.us-east-1.elb.amazonaws.com
```

## How to Add the API Key Header

### Option 1: Using Fetch API

```typescript
// In your ocr-api.ts file
const API_KEY = 'LAf4zsG1Zg3ePbFqkWQvNemTRy5S0c2d';
const API_BASE_URL = 'http://epsteingptengine-alb-1564453343.us-east-1.elb.amazonaws.com';

async function listOcrFiles() {
  const response = await fetch(`${API_BASE_URL}/files?limit=10000`, {
    method: 'GET',
    headers: {
      'X-API-Key': API_KEY,  // ← Make sure this is exactly 'X-API-Key'
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to list OCR files: ${response.statusText}`);
  }

  return await response.json();
}
```

### Option 2: Using Axios

```typescript
import axios from 'axios';

const API_KEY = 'LAf4zsG1Zg3ePbFqkWQvNemTRy5S0c2d';
const API_BASE_URL = 'http://epsteingptengine-alb-1564453343.us-east-1.elb.amazonaws.com';

// Create an axios instance with default headers
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'X-API-Key': API_KEY,
    'Content-Type': 'application/json',
  },
});

// Then use it for all requests
async function listOcrFiles() {
  const response = await apiClient.get('/files', {
    params: { limit: 10000 }
  });
  return response.data;
}
```

### Option 3: Using Environment Variables (Recommended)

```typescript
// .env.local or .env
NEXT_PUBLIC_API_KEY=LAf4zsG1Zg3ePbFqkWQvNemTRy5S0c2d
NEXT_PUBLIC_API_BASE_URL=http://epsteingptengine-alb-1564453343.us-east-1.elb.amazonaws.com

// In your code
const API_KEY = process.env.NEXT_PUBLIC_API_KEY!;
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL!;
```

## Common Mistakes to Avoid

1. **Wrong header name:**
   ❌ `'X-API-KEY'` (all caps)
   ❌ `'x-api-key'` (all lowercase - might work but use standard)
   ✅ `'X-API-Key'` (correct)

2. **Header not included in all requests:**
   Make sure EVERY request to the API includes the header, not just some.

3. **CORS issues:**
   If you see CORS errors, the header might be blocked. The backend is configured to allow it, but check your browser console.

4. **Typo in the API key:**
   Double-check the key: `LAf4zsG1Zg3ePbFqkWQvNemTRy5S0c2d`

## Debugging Steps

1. **Check the Network Tab:**
   - Open DevTools → Network
   - Make a request
   - Click on the failed request
   - Go to "Headers" tab
   - Look under "Request Headers"
   - Verify `X-API-Key` is there with the correct value

2. **Test with curl:**
   ```bash
   curl -H "X-API-Key: LAf4zsG1Zg3ePbFqkWQvNemTRy5S0c2d" \
     "http://epsteingptengine-alb-1564453343.us-east-1.elb.amazonaws.com/files?limit=5"
   ```
   If this works, the backend is fine - the issue is in your frontend code.

3. **Check for OPTIONS requests:**
   - Look for an OPTIONS request before your actual request
   - OPTIONS requests should succeed (they're CORS preflight)
   - Your actual GET/POST request should include the header

## Example: Complete API Client

```typescript
// api-client.ts
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'LAf4zsG1Zg3ePbFqkWQvNemTRy5S0c2d';
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 
  'http://epsteingptengine-alb-1564453343.us-east-1.elb.amazonaws.com';

export async function fetchFiles(limit = 10000) {
  const response = await fetch(`${API_BASE_URL}/files?limit=${limit}`, {
    headers: {
      'X-API-Key': API_KEY,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(`Failed to fetch files: ${error.detail || response.statusText}`);
  }

  return await response.json();
}

export async function searchFiles(query: string, limit = 100) {
  const response = await fetch(`${API_BASE_URL}/search/files?q=${encodeURIComponent(query)}&limit=${limit}`, {
    headers: {
      'X-API-Key': API_KEY,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(`Failed to search files: ${error.detail || response.statusText}`);
  }

  return await response.json();
}
```

