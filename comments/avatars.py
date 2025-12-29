"""Generate Apple-style profile pictures (circular avatars with initials)."""
import hashlib
import random
from io import BytesIO
from typing import Tuple
from PIL import Image, ImageDraw, ImageFont
import boto3
from config import Config


# Apple-style color palettes (warm, cool, vibrant)
_AVATAR_COLORS = [
    # Warm tones
    ("#FF6B6B", "#FF8E8E"),  # Red
    ("#FFA07A", "#FFB89A"),  # Light Salmon
    ("#FFD93D", "#FFE066"),  # Yellow
    ("#FF8C42", "#FFA366"),  # Orange
    # Cool tones
    ("#4ECDC4", "#6EDDD6"),  # Turquoise
    ("#45B7D1", "#6BC5D9"),  # Sky Blue
    ("#96CEB4", "#B4DEC6"),  # Mint
    ("#95E1D3", "#B3E9DB"),  # Aqua
    # Vibrant
    ("#A8E6CF", "#C0EED9"),  # Light Green
    ("#FFD3A5", "#FFE0B8"),  # Peach
    ("#C7CEEA", "#D9DFF0"),  # Lavender
    ("#F38181", "#F59A9A"),  # Coral
    ("#AA96DA", "#C0B4E5"),  # Purple
    ("#FCBAD3", "#FDC9DD"),  # Pink
    ("#FFD89B", "#FFE2B3"),  # Gold
    ("#A8DADC", "#C0E5E7"),  # Cyan
]


def _generate_username() -> str:
    """Generate a random username in the style: AdjectiveNoun (e.g., BlueOtter, SwiftTiger)."""
    adjectives = [
        "Swift", "Bold", "Bright", "Calm", "Clever", "Cool", "Daring", "Eager", "Fierce",
        "Gentle", "Happy", "Jolly", "Kind", "Lively", "Lucky", "Mighty", "Noble", "Proud",
        "Quick", "Quiet", "Rapid", "Sharp", "Smart", "Smooth", "Strong", "Swift", "Tough",
        "Wise", "Wild", "Witty", "Zesty", "Zippy", "Azure", "Crimson", "Golden", "Silver",
        "Amber", "Emerald", "Ruby", "Sapphire", "Violet", "Coral", "Ivory", "Jade"
    ]
    nouns = [
        "Otter", "Tiger", "Eagle", "Lion", "Wolf", "Bear", "Fox", "Hawk", "Falcon",
        "Dolphin", "Shark", "Whale", "Seal", "Penguin", "Owl", "Raven", "Crow",
        "Swan", "Heron", "Stag", "Deer", "Elk", "Moose", "Bison", "Buffalo",
        "Panther", "Jaguar", "Leopard", "Cheetah", "Lynx", "Bobcat", "Cougar",
        "Badger", "Marten", "Weasel", "Ferret", "Mink", "Sable", "Ermine",
        "Phoenix", "Griffin", "Dragon", "Unicorn", "Pegasus", "Kraken", "Sphinx"
    ]
    return f"{random.choice(adjectives)}{random.choice(nouns)}"


def _username_to_color(username: str) -> Tuple[str, str]:
    """Deterministically pick a color pair for a username (same username = same color)."""
    hash_val = int(hashlib.md5(username.encode()).hexdigest(), 16)
    idx = hash_val % len(_AVATAR_COLORS)
    return _AVATAR_COLORS[idx]


def _generate_avatar_image(username: str, size: int = 128) -> BytesIO:
    """Generate an Apple-style circular avatar with initials.
    
    Returns a BytesIO buffer containing a PNG image.
    """
    # Get initials (first letter, or first two if multi-word)
    words = username.replace("_", " ").split()
    if len(words) >= 2:
        initials = (words[0][0] + words[1][0]).upper()
    else:
        initials = username[:2].upper() if len(username) >= 2 else username[0].upper()
    
    # Create circular image
    img = Image.new("RGB", (size, size), color="#FFFFFF")
    draw = ImageDraw.Draw(img)
    
    # Get color pair for this username
    color_base, color_light = _username_to_color(username)
    
    # Draw circular background with gradient effect (simplified: solid circle)
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color_base,
        outline=None
    )
    
    # Try to load a nice font, fallback to default
    try:
        # Try system fonts (varies by OS)
        font_size = int(size * 0.4)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", font_size)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except:
                font = ImageFont.load_default()
    except:
        font = ImageFont.load_default()
    
    # Get text bounding box to center it
    bbox = draw.textbbox((0, 0), initials, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    # Draw initials in white, centered
    text_x = (size - text_width) // 2
    text_y = (size - text_height) // 2 - bbox[1]
    draw.text(
        (text_x, text_y),
        initials,
        fill="#FFFFFF",
        font=font,
        anchor="lt"
    )
    
    # Convert to circular mask (Apple-style: perfectly round)
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse([0, 0, size, size], fill=255)
    
    # Apply circular mask
    output = Image.new("RGB", (size, size), color="#FFFFFF")
    output.paste(img, (0, 0))
    output.putalpha(mask)
    
    # Save to BytesIO
    buffer = BytesIO()
    output.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def generate_and_upload_avatar(username: str) -> str:
    """Generate an avatar for a username and upload to S3.
    
    Returns the S3 URL (presigned if S3_BUCKET configured, otherwise local path).
    """
    avatar_img = _generate_avatar_image(username)
    
    if Config.S3_BUCKET:
        # Upload to S3
        s3_key = f"avatars/{username.lower()}.png"
        s3_client = boto3.client("s3", region_name=Config.S3_REGION)
        s3_client.upload_fileobj(
            avatar_img,
            Config.S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": "image/png"}
        )
        
        # Generate presigned URL
        from storage.s3_assets import presign_get
        url = presign_get(s3_key, expires_seconds=Config.S3_PRESIGN_EXPIRES_SECONDS)
        return url
    else:
        # Local storage (fallback)
        import os
        os.makedirs("data/avatars", exist_ok=True)
        local_path = f"data/avatars/{username.lower()}.png"
        with open(local_path, "wb") as f:
            f.write(avatar_img.read())
        return f"/avatars/{username.lower()}.png"


def get_avatar_url(username: str) -> str:
    """Get or generate avatar URL for a username.
    
    If avatar doesn't exist in S3, generates and uploads it.
    """
    if Config.S3_BUCKET:
        s3_key = f"avatars/{username.lower()}.png"
        s3_client = boto3.client("s3", region_name=Config.S3_REGION)
        
        # Check if exists
        try:
            s3_client.head_object(Bucket=Config.S3_BUCKET, Key=s3_key)
            # Exists, return presigned URL
            from storage.s3_assets import presign_get
            return presign_get(s3_key, expires_seconds=Config.S3_PRESIGN_EXPIRES_SECONDS)
        except s3_client.exceptions.ClientError:
            # Doesn't exist, generate and upload
            return generate_and_upload_avatar(username)
    else:
        # Local fallback
        import os
        local_path = f"data/avatars/{username.lower()}.png"
        if os.path.exists(local_path):
            return f"/avatars/{username.lower()}.png"
        else:
            return generate_and_upload_avatar(username)

