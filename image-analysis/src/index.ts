/**
 * AWS Rekognition Image Analysis for Epstein Files Thumbnails
 * 
 * This script fetches thumbnails from the existing engine's source endpoint
 * and analyzes them using AWS Rekognition for:
 * - Labels (objects/scenes)
 * - Faces (with full attributes)
 * - Celebrity recognition
 * 
 * AWS credentials should be configured via environment variables or IAM role.
 */

import {
  RekognitionClient,
  DetectLabelsCommand,
  DetectFacesCommand,
  RecognizeCelebritiesCommand,
  Label,
  FaceDetail,
  Celebrity,
  Attribute,
} from "@aws-sdk/client-rekognition";

// ============================================================================
// Configuration
// ============================================================================

const SOURCE_ENDPOINT = process.env.SOURCE_ENDPOINT || "https://epstein-files.rhys-669.workers.dev";
const THUMBNAILS_PATH = "/thumbnails";
const CONCURRENCY_LIMIT = parseInt(process.env.CONCURRENCY_LIMIT || "3", 10);
const OUTPUT_DIR = process.env.OUTPUT_DIR || "./results";

// ============================================================================
// Types
// ============================================================================

interface NormalizedLabel {
  name: string;
  confidence: number;
}

interface BoundingBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface NormalizedFace {
  boundingBox: BoundingBox | null;
  ageRange: { low: number; high: number } | null;
  gender: { value: string; confidence: number } | null;
  emotions: Array<{ type: string; confidence: number }>;
  smile: { value: boolean; confidence: number } | null;
  eyeglasses: { value: boolean; confidence: number } | null;
  sunglasses: { value: boolean; confidence: number } | null;
  beard: { value: boolean; confidence: number } | null;
  mustache: { value: boolean; confidence: number } | null;
  eyesOpen: { value: boolean; confidence: number } | null;
  mouthOpen: { value: boolean; confidence: number } | null;
  confidence: number;
}

interface NormalizedCelebrity {
  name: string;
  matchConfidence: number;
  urls: string[];
  boundingBox: BoundingBox | null;
}

interface AnalysisResult {
  filename: string;
  thumbnailUrl: string;
  analyzedAt: string;
  labels: NormalizedLabel[];
  faces: NormalizedFace[];
  celebrities: NormalizedCelebrity[];
  errors: string[];
}

interface FileInfo {
  key: string;
  filename: string;
}

// ============================================================================
// Rekognition Client
// ============================================================================

const rekognition = new RekognitionClient({
  // Region and credentials from environment or IAM role
  region: process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || "us-east-1",
});

// ============================================================================
// Concurrency Limiter (simple implementation to avoid p-limit ESM issues)
// ============================================================================

function createLimiter(concurrency: number) {
  let active = 0;
  const queue: Array<() => void> = [];

  const next = () => {
    if (queue.length > 0 && active < concurrency) {
      active++;
      const run = queue.shift()!;
      run();
    }
  };

  return async function limit<T>(fn: () => Promise<T>): Promise<T> {
    return new Promise((resolve, reject) => {
      const run = async () => {
        try {
          const result = await fn();
          resolve(result);
        } catch (err) {
          reject(err);
        } finally {
          active--;
          next();
        }
      };

      if (active < concurrency) {
        active++;
        run();
      } else {
        queue.push(run);
      }
    });
  };
}

const limit = createLimiter(CONCURRENCY_LIMIT);

// ============================================================================
// File Discovery
// ============================================================================

async function discoverFiles(): Promise<FileInfo[]> {
  const candidateUrls = [
    `${SOURCE_ENDPOINT}/api/all-files`,
    `${SOURCE_ENDPOINT}/files.json`,
    `${SOURCE_ENDPOINT}/list.json`,
    `${SOURCE_ENDPOINT}/api/files`,
    `${SOURCE_ENDPOINT}/`,
  ];

  for (const url of candidateUrls) {
    try {
      const response = await fetch(url, {
        headers: {
          "User-Agent": "epstein-image-analysis/1.0",
          "Accept": "application/json",
        },
      });

      if (!response.ok) continue;

      const contentType = response.headers.get("content-type") || "";
      const text = await response.text();

      // Try parsing as JSON
      if (contentType.includes("json") || text.trim().startsWith("[") || text.trim().startsWith("{")) {
        try {
          const data = JSON.parse(text);
          return extractFilesFromJson(data);
        } catch {
          continue;
        }
      }
    } catch {
      continue;
    }
  }

  console.error("Could not discover files from any endpoint");
  return [];
}

function extractFilesFromJson(data: unknown): FileInfo[] {
  const files: FileInfo[] = [];

  const processItem = (item: unknown) => {
    if (typeof item === "string") {
      const filename = item.split("/").pop() || item;
      if (isImageFile(filename)) {
        files.push({ key: item, filename });
      }
    } else if (typeof item === "object" && item !== null) {
      const obj = item as Record<string, unknown>;
      const key = (obj.key || obj.url || obj.href || obj.path) as string | undefined;
      const name = (obj.filename || obj.name || (key ? key.split("/").pop() : undefined)) as string | undefined;
      
      if (key && name && isImageFile(name)) {
        files.push({ key, filename: name });
      }
    }
  };

  if (Array.isArray(data)) {
    data.forEach(processItem);
  } else if (typeof data === "object" && data !== null) {
    const obj = data as Record<string, unknown>;
    for (const key of ["files", "items", "data", "results"]) {
      if (Array.isArray(obj[key])) {
        (obj[key] as unknown[]).forEach(processItem);
      }
    }
  }

  return files;
}

function isImageFile(filename: string): boolean {
  const ext = filename.toLowerCase().split(".").pop();
  return ["pdf", "jpg", "jpeg", "png", "tiff", "tif", "bmp", "gif"].includes(ext || "");
}

// ============================================================================
// Thumbnail Fetching
// ============================================================================

async function fetchThumbnail(filename: string): Promise<Uint8Array | null> {
  // Convert filename to thumbnail format (replace extension with .jpg)
  const baseName = filename.replace(/\.[^.]+$/, "");
  const thumbnailUrl = `${SOURCE_ENDPOINT}${THUMBNAILS_PATH}/${baseName}.jpg`;

  try {
    const response = await fetch(thumbnailUrl, {
      headers: {
        "User-Agent": "epstein-image-analysis/1.0",
      },
    });

    if (!response.ok) {
      console.warn(`Thumbnail not found for ${filename}: ${response.status}`);
      return null;
    }

    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("image")) {
      console.warn(`Non-image response for ${filename}: ${contentType}`);
      return null;
    }

    const arrayBuffer = await response.arrayBuffer();
    return new Uint8Array(arrayBuffer);
  } catch (error) {
    console.warn(`Failed to fetch thumbnail for ${filename}:`, error);
    return null;
  }
}

// ============================================================================
// Rekognition Analysis
// ============================================================================

async function detectLabels(imageBytes: Uint8Array): Promise<Label[]> {
  try {
    const command = new DetectLabelsCommand({
      Image: { Bytes: imageBytes },
      MaxLabels: 50,
      MinConfidence: 50,
    });
    const response = await rekognition.send(command);
    return response.Labels || [];
  } catch (error) {
    console.warn("DetectLabels failed:", error);
    return [];
  }
}

async function detectFaces(imageBytes: Uint8Array): Promise<FaceDetail[]> {
  try {
    const command = new DetectFacesCommand({
      Image: { Bytes: imageBytes },
      Attributes: [Attribute.ALL],
    });
    const response = await rekognition.send(command);
    return response.FaceDetails || [];
  } catch (error) {
    console.warn("DetectFaces failed:", error);
    return [];
  }
}

async function recognizeCelebrities(imageBytes: Uint8Array): Promise<Celebrity[]> {
  try {
    const command = new RecognizeCelebritiesCommand({
      Image: { Bytes: imageBytes },
    });
    const response = await rekognition.send(command);
    return response.CelebrityFaces || [];
  } catch (error) {
    console.warn("RecognizeCelebrities failed:", error);
    return [];
  }
}

// ============================================================================
// Result Normalization
// ============================================================================

function normalizeLabels(labels: Label[]): NormalizedLabel[] {
  return labels.map((label) => ({
    name: label.Name || "Unknown",
    confidence: label.Confidence || 0,
  }));
}

function normalizeBoundingBox(box: { Left?: number; Top?: number; Width?: number; Height?: number } | undefined): BoundingBox | null {
  if (!box) return null;
  return {
    left: box.Left || 0,
    top: box.Top || 0,
    width: box.Width || 0,
    height: box.Height || 0,
  };
}

function normalizeFaces(faces: FaceDetail[]): NormalizedFace[] {
  return faces.map((face) => ({
    boundingBox: normalizeBoundingBox(face.BoundingBox),
    ageRange: face.AgeRange
      ? { low: face.AgeRange.Low || 0, high: face.AgeRange.High || 0 }
      : null,
    gender: face.Gender
      ? { value: face.Gender.Value || "Unknown", confidence: face.Gender.Confidence || 0 }
      : null,
    emotions: (face.Emotions || []).map((e) => ({
      type: e.Type || "Unknown",
      confidence: e.Confidence || 0,
    })),
    smile: face.Smile
      ? { value: face.Smile.Value || false, confidence: face.Smile.Confidence || 0 }
      : null,
    eyeglasses: face.Eyeglasses
      ? { value: face.Eyeglasses.Value || false, confidence: face.Eyeglasses.Confidence || 0 }
      : null,
    sunglasses: face.Sunglasses
      ? { value: face.Sunglasses.Value || false, confidence: face.Sunglasses.Confidence || 0 }
      : null,
    beard: face.Beard
      ? { value: face.Beard.Value || false, confidence: face.Beard.Confidence || 0 }
      : null,
    mustache: face.Mustache
      ? { value: face.Mustache.Value || false, confidence: face.Mustache.Confidence || 0 }
      : null,
    eyesOpen: face.EyesOpen
      ? { value: face.EyesOpen.Value || false, confidence: face.EyesOpen.Confidence || 0 }
      : null,
    mouthOpen: face.MouthOpen
      ? { value: face.MouthOpen.Value || false, confidence: face.MouthOpen.Confidence || 0 }
      : null,
    confidence: face.Confidence || 0,
  }));
}

function normalizeCelebrities(celebrities: Celebrity[]): NormalizedCelebrity[] {
  return celebrities.map((celeb) => ({
    name: celeb.Name || "Unknown",
    matchConfidence: celeb.MatchConfidence || 0,
    urls: celeb.Urls || [],
    boundingBox: normalizeBoundingBox(celeb.Face?.BoundingBox),
  }));
}

// ============================================================================
// Main Analysis Pipeline
// ============================================================================

async function analyzeFile(file: FileInfo): Promise<AnalysisResult> {
  const baseName = file.filename.replace(/\.[^.]+$/, "");
  const thumbnailUrl = `${SOURCE_ENDPOINT}${THUMBNAILS_PATH}/${baseName}.jpg`;
  
  const result: AnalysisResult = {
    filename: file.filename,
    thumbnailUrl,
    analyzedAt: new Date().toISOString(),
    labels: [],
    faces: [],
    celebrities: [],
    errors: [],
  };

  // Fetch thumbnail
  const imageBytes = await fetchThumbnail(file.filename);
  if (!imageBytes) {
    result.errors.push("Failed to fetch or invalid thumbnail");
    return result;
  }

  // Run all three Rekognition operations in parallel
  const [labels, faces, celebrities] = await Promise.all([
    detectLabels(imageBytes),
    detectFaces(imageBytes),
    recognizeCelebrities(imageBytes),
  ]);

  // Normalize results
  result.labels = normalizeLabels(labels);
  result.faces = normalizeFaces(faces);
  result.celebrities = normalizeCelebrities(celebrities);

  return result;
}

async function analyzeAllFiles(files: FileInfo[]): Promise<AnalysisResult[]> {
  console.log(`\nAnalyzing ${files.length} files with concurrency limit of ${CONCURRENCY_LIMIT}...\n`);

  const results: AnalysisResult[] = [];
  let completed = 0;

  const tasks = files.map((file) =>
    limit(async () => {
      const result = await analyzeFile(file);
      completed++;
      
      const status = result.errors.length > 0 ? "⚠️" : "✅";
      const celebCount = result.celebrities.length;
      const faceCount = result.faces.length;
      const labelCount = result.labels.length;
      
      console.log(
        `${status} [${completed}/${files.length}] ${file.filename} - ` +
        `${labelCount} labels, ${faceCount} faces, ${celebCount} celebrities`
      );

      return result;
    })
  );

  const allResults = await Promise.all(tasks);
  results.push(...allResults);

  return results;
}

// ============================================================================
// Output
// ============================================================================

async function writeResults(results: AnalysisResult[]): Promise<void> {
  const fs = await import("fs/promises");
  const path = await import("path");

  // Ensure output directory exists
  await fs.mkdir(OUTPUT_DIR, { recursive: true });

  // Write individual result files
  for (const result of results) {
    const baseName = result.filename.replace(/\.[^.]+$/, "");
    const outputPath = path.join(OUTPUT_DIR, `${baseName}.analysis.json`);
    await fs.writeFile(outputPath, JSON.stringify(result, null, 2));
  }

  // Write summary file
  const summary = {
    analyzedAt: new Date().toISOString(),
    totalFiles: results.length,
    successfulAnalyses: results.filter((r) => r.errors.length === 0).length,
    failedAnalyses: results.filter((r) => r.errors.length > 0).length,
    totalLabelsFound: results.reduce((sum, r) => sum + r.labels.length, 0),
    totalFacesFound: results.reduce((sum, r) => sum + r.faces.length, 0),
    totalCelebritiesFound: results.reduce((sum, r) => sum + r.celebrities.length, 0),
    celebrityMatches: results
      .flatMap((r) => r.celebrities.map((c) => ({ file: r.filename, ...c })))
      .sort((a, b) => b.matchConfidence - a.matchConfidence),
    files: results.map((r) => ({
      filename: r.filename,
      labels: r.labels.length,
      faces: r.faces.length,
      celebrities: r.celebrities.length,
      errors: r.errors,
    })),
  };

  const summaryPath = path.join(OUTPUT_DIR, "summary.json");
  await fs.writeFile(summaryPath, JSON.stringify(summary, null, 2));

  console.log(`\nResults written to ${OUTPUT_DIR}/`);
  console.log(`  - ${results.length} individual analysis files`);
  console.log(`  - summary.json`);
}

// ============================================================================
// CLI Entry Point
// ============================================================================

async function main(): Promise<void> {
  console.log("═══════════════════════════════════════════════════════════════");
  console.log("  Epstein Files Image Analysis (AWS Rekognition)");
  console.log("═══════════════════════════════════════════════════════════════");
  console.log(`Source: ${SOURCE_ENDPOINT}`);
  console.log(`Thumbnails: ${SOURCE_ENDPOINT}${THUMBNAILS_PATH}/{filename}.jpg`);
  console.log(`Concurrency: ${CONCURRENCY_LIMIT}`);
  console.log(`Output: ${OUTPUT_DIR}`);
  console.log("═══════════════════════════════════════════════════════════════\n");

  // Discover files from source endpoint
  console.log("Discovering files...");
  const files = await discoverFiles();
  
  if (files.length === 0) {
    console.error("No files discovered. Exiting.");
    process.exit(1);
  }

  console.log(`Found ${files.length} files`);

  // Analyze all files
  const results = await analyzeAllFiles(files);

  // Write results
  await writeResults(results);

  // Print summary
  const successful = results.filter((r) => r.errors.length === 0).length;
  const celebrityCount = results.reduce((sum, r) => sum + r.celebrities.length, 0);
  
  console.log("\n═══════════════════════════════════════════════════════════════");
  console.log("  Summary");
  console.log("═══════════════════════════════════════════════════════════════");
  console.log(`  Total files analyzed: ${results.length}`);
  console.log(`  Successful: ${successful}`);
  console.log(`  Failed: ${results.length - successful}`);
  console.log(`  Total celebrities detected: ${celebrityCount}`);
  
  if (celebrityCount > 0) {
    console.log("\n  Top celebrity matches:");
    const topCelebs = results
      .flatMap((r) => r.celebrities.map((c) => ({ file: r.filename, ...c })))
      .sort((a, b) => b.matchConfidence - a.matchConfidence)
      .slice(0, 10);
    
    topCelebs.forEach((c, i) => {
      console.log(`    ${i + 1}. ${c.name} (${c.matchConfidence.toFixed(1)}%) - ${c.file}`);
    });
  }
  
  console.log("═══════════════════════════════════════════════════════════════\n");
}

// Run if executed directly
main().catch((error) => {
  console.error("Fatal error:", error);
  process.exit(1);
});




