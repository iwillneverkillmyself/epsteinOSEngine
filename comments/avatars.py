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
    """Generate a random username in Kahoot style: AdjectiveNoun (e.g., RetiredMonkey, SwiftTiger).
    
    Uses a large pool of adjectives and nouns to minimize duplicates, similar to Kahoot.
    """
    adjectives = [
        # Personality traits
        "Swift", "Bold", "Bright", "Calm", "Clever", "Cool", "Daring", "Eager", "Fierce",
        "Gentle", "Happy", "Jolly", "Kind", "Lively", "Lucky", "Mighty", "Noble", "Proud",
        "Quick", "Quiet", "Rapid", "Sharp", "Smart", "Smooth", "Strong", "Tough",
        "Wise", "Wild", "Witty", "Zesty", "Zippy", "Brave", "Bold", "Brisk", "Bubbly",
        "Cheerful", "Chill", "Clever", "Cozy", "Crafty", "Crazy", "Crisp", "Curious",
        "Dapper", "Dazzling", "Diligent", "Dizzy", "Dramatic", "Dreamy", "Drowsy", "Dutiful",
        "Eager", "Elegant", "Epic", "Energetic", "Enthusiastic", "Excited", "Exotic", "Extra",
        "Fancy", "Fast", "Fierce", "Fiery", "Fluffy", "Focused", "Foolish", "Fragile", "Fresh",
        "Friendly", "Frosty", "Funky", "Funny", "Furious", "Fuzzy", "Gallant", "Gentle",
        "Giant", "Giddy", "Glamorous", "Gleaming", "Glorious", "Glowing", "Golden", "Goofy",
        "Graceful", "Grand", "Grateful", "Great", "Greedy", "Green", "Grim", "Groovy",
        "Grumpy", "Guilty", "Happy", "Harsh", "Hasty", "Healthy", "Heavy", "Helpful",
        "Heroic", "Hilarious", "Hollow", "Honest", "Hopeful", "Horned", "Hot", "Huge",
        "Humble", "Hungry", "Icy", "Ideal", "Idle", "Ill", "Imaginary", "Immense", "Impressive",
        "Incredible", "Innocent", "Inquisitive", "Inspired", "Intense", "Interesting", "Jolly",
        "Jovial", "Joyful", "Jubilant", "Jumpy", "Kind", "Kooky", "Lanky", "Large", "Lazy",
        "Lean", "Lively", "Lonely", "Long", "Loud", "Lovable", "Lovely", "Loyal", "Lucky",
        "Lumpy", "Lush", "Magical", "Majestic", "Mammoth", "Marvelous", "Massive", "Mellow",
        "Merry", "Mighty", "Mini", "Misty", "Modern", "Modest", "Moody", "Muddy", "Mysterious",
        "Narrow", "Nasty", "Naughty", "Nervous", "Nice", "Nifty", "Nimble", "Noble", "Noisy",
        "Normal", "Numb", "Nutty", "Obedient", "Odd", "Old", "Optimistic", "Orange", "Ordinary",
        "Organic", "Outgoing", "Outrageous", "Outstanding", "Oval", "Overjoyed", "Pale", "Panicky",
        "Peaceful", "Peppy", "Perfect", "Perky", "Pesky", "Petite", "Picky", "Pink", "Playful",
        "Pleasant", "Plump", "Polite", "Poor", "Popular", "Powerful", "Precious", "Pretty",
        "Prickly", "Proud", "Puffy", "Pumped", "Punchy", "Purple", "Puzzled", "Quaint", "Quick",
        "Quiet", "Quirky", "Quivery", "Radiant", "Rapid", "Rare", "Rash", "Raw", "Ready",
        "Real", "Rebel", "Red", "Refined", "Relaxed", "Reliable", "Relieved", "Remarkable",
        "Retired", "Rich", "Rigid", "Ripe", "Robust", "Rocky", "Romantic", "Rosy", "Rough",
        "Round", "Rowdy", "Royal", "Rude", "Rugged", "Rusty", "Sad", "Safe", "Salty",
        "Sassy", "Satisfied", "Scary", "Scattered", "Scenic", "Scrappy", "Serene", "Serious",
        "Shady", "Shaggy", "Shaky", "Shallow", "Sharp", "Shiny", "Shocked", "Shocking",
        "Short", "Shrill", "Shy", "Sick", "Silent", "Silly", "Silver", "Simple", "Sincere",
        "Sizzling", "Skilled", "Skinny", "Sleepy", "Slick", "Slim", "Slimy", "Slippery",
        "Slow", "Small", "Smart", "Smelly", "Smiling", "Smooth", "Snappy", "Sneaky", "Snug",
        "Soaring", "Social", "Soft", "Solar", "Solid", "Solitary", "Somber", "Sophisticated",
        "Sore", "Sour", "Sparkling", "Special", "Speedy", "Spicy", "Spiffy", "Spiky", "Spirited",
        "Splendid", "Spooky", "Spotless", "Spotty", "Spry", "Square", "Squeaky", "Squiggly",
        "Stable", "Stale", "Standard", "Stark", "Starry", "Steady", "Stealthy", "Steamy",
        "Steel", "Steep", "Sticky", "Stiff", "Stimulating", "Stingy", "Stormy", "Straight",
        "Strange", "Strict", "Strong", "Stunning", "Stupendous", "Sturdy", "Subtle", "Successful",
        "Succulent", "Sudden", "Sugary", "Sunny", "Super", "Superb", "Superior", "Supreme",
        "Sure", "Surprised", "Suspicious", "Svelte", "Swanky", "Sweet", "Swift", "Swollen",
        "Sympathetic", "Tactful", "Talented", "Tall", "Tame", "Tangy", "Tart", "Tasteful",
        "Tasty", "Tattered", "Taut", "Tedious", "Teeny", "Tender", "Tense", "Terrible",
        "Terrific", "Testy", "Thankful", "That", "Thick", "Thin", "Thirsty", "Thorny",
        "Thoughtful", "Threadbare", "Thrifty", "Thunderous", "Tidy", "Tight", "Timely",
        "Tinted", "Tiny", "Tired", "Tough", "Tragic", "Trained", "Tremendous", "Tricky",
        "Trim", "Triumphant", "Troubled", "True", "Trusting", "Trustworthy", "Truthful",
        "Tubby", "Turbulent", "Twin", "Ugly", "Ultimate", "Unacceptable", "Unaware",
        "Uncomfortable", "Uncommon", "Unconscious", "Understated", "Unequaled", "Uneven",
        "Unfinished", "Unfit", "Unfolded", "Unfortunate", "Unhappy", "Unhealthy", "Uniform",
        "Unimportant", "Unique", "United", "Unkempt", "Unknown", "Unlawful", "Unlined",
        "Unlucky", "Unnatural", "Unpleasant", "Unrealistic", "Unripe", "Unruly", "Unselfish",
        "Unusual", "Unwelcome", "Unwieldy", "Unwilling", "Unwitting", "Unwritten", "Upbeat",
        "Upright", "Upset", "Urban", "Usable", "Used", "Useful", "Useless", "Utilized",
        "Utter", "Vacant", "Vague", "Vain", "Valid", "Valuable", "Vapid", "Variable",
        "Vast", "Velvety", "Venerated", "Venomous", "Versatile", "Vibrant", "Vicious",
        "Victorious", "Vigilant", "Vigorous", "Villainous", "Violet", "Violent", "Virtual",
        "Virtuous", "Visible", "Vital", "Vivacious", "Vivid", "Voluminous", "Wan", "Warlike",
        "Warm", "Warmhearted", "Warped", "Wary", "Wasteful", "Watchful", "Waterlogged",
        "Watery", "Wavy", "Weak", "Wealthy", "Weary", "Webbed", "Wee", "Weekly", "Weepy",
        "Weighty", "Weird", "Welcome", "Well-documented", "Well-groomed", "Well-informed",
        "Well-lit", "Well-made", "Well-off", "Well-to-do", "Well-worn", "Wet", "Which",
        "Whimsical", "Whirlwind", "Whispered", "White", "Whole", "Whopping", "Wicked",
        "Wide", "Wide-eyed", "Widespread", "Wild", "Willing", "Wilted", "Winding", "Windy",
        "Winged", "Wiry", "Wise", "Witty", "Wobbly", "Woeful", "Wonderful", "Wooden",
        "Woozy", "Wordy", "Worldly", "Worn", "Worried", "Worrisome", "Worse", "Worst",
        "Worthless", "Worthwhile", "Worthy", "Wrathful", "Wretched", "Writhing", "Wrong",
        "Wry", "Yawning", "Yearly", "Yellow", "Yellowish", "Young", "Youthful", "Yummy",
        "Zany", "Zealous", "Zesty", "Zigzag", "Zippy", "Zonked"
    ]
    nouns = [
        # Animals
        "Otter", "Tiger", "Eagle", "Lion", "Wolf", "Bear", "Fox", "Hawk", "Falcon",
        "Dolphin", "Shark", "Whale", "Seal", "Penguin", "Owl", "Raven", "Crow",
        "Swan", "Heron", "Stag", "Deer", "Elk", "Moose", "Bison", "Buffalo",
        "Panther", "Jaguar", "Leopard", "Cheetah", "Lynx", "Bobcat", "Cougar",
        "Badger", "Marten", "Weasel", "Ferret", "Mink", "Sable", "Ermine",
        "Phoenix", "Griffin", "Dragon", "Unicorn", "Pegasus", "Kraken", "Sphinx",
        "Monkey", "Gorilla", "Chimp", "Orangutan", "Baboon", "Macaque", "Gibbon",
        "Panda", "Koala", "Kangaroo", "Wallaby", "Wombat", "Possum", "Tasmanian",
        "Elephant", "Rhino", "Hippo", "Giraffe", "Zebra", "Camel", "Llama", "Alpaca",
        "Horse", "Pony", "Donkey", "Mule", "Goat", "Sheep", "Pig", "Cow", "Bull",
        "Chicken", "Rooster", "Duck", "Goose", "Turkey", "Peacock", "Parrot", "Cockatoo",
        "Rabbit", "Bunny", "Hare", "Hamster", "Guinea", "Mouse", "Rat", "Squirrel",
        "Chipmunk", "Beaver", "Porcupine", "Hedgehog", "Armadillo", "Sloth", "Anteater",
        "Turtle", "Tortoise", "Lizard", "Gecko", "Iguana", "Chameleon", "Snake", "Python",
        "Cobra", "Viper", "Alligator", "Crocodile", "Frog", "Toad", "Salamander", "Newt",
        "Fish", "Salmon", "Tuna", "Trout", "Bass", "Pike", "Carp", "Goldfish", "Koi",
        "Octopus", "Squid", "Cuttlefish", "Jellyfish", "Starfish", "Crab", "Lobster",
        "Shrimp", "Prawn", "Scallop", "Clam", "Oyster", "Mussel", "Snail", "Slug",
        "Butterfly", "Moth", "Bee", "Wasp", "Hornet", "Ant", "Beetle", "Ladybug",
        "Spider", "Scorpion", "Centipede", "Millipede", "Worm", "Earthworm", "Caterpillar",
        # Birds
        "Canary", "Finch", "Sparrow", "Robin", "Bluejay", "Cardinal", "Hummingbird",
        "Woodpecker", "Toucan", "Flamingo", "Stork", "Crane", "Pelican", "Albatross",
        "Seagull", "Tern", "Puffin", "Gannet", "Cormorant", "Ibis", "Egret", "Bittern",
        # Mythical & Fantasy
        "Goblin", "Elf", "Dwarf", "Troll", "Orc", "Wizard", "Witch", "Sorcerer",
        "Knight", "Warrior", "Paladin", "Ranger", "Rogue", "Bard", "Cleric", "Monk",
        "Ninja", "Samurai", "Viking", "Pirate", "Sailor", "Explorer", "Adventurer",
        "Hero", "Villain", "Guardian", "Protector", "Defender", "Champion", "Legend",
        # Objects & Things
        "Comet", "Star", "Planet", "Moon", "Sun", "Galaxy", "Nebula", "Asteroid",
        "Mountain", "Volcano", "River", "Ocean", "Lake", "Island", "Forest", "Desert",
        "Crystal", "Gem", "Diamond", "Pearl", "Amber", "Jade", "Ruby", "Sapphire",
        "Emerald", "Topaz", "Opal", "Garnet", "Amethyst", "Quartz", "Marble", "Granite",
        "Storm", "Thunder", "Lightning", "Rain", "Snow", "Wind", "Breeze", "Gale",
        "Flame", "Spark", "Ember", "Blaze", "Inferno", "Fire", "Ice", "Frost",
        "Shadow", "Light", "Glow", "Beam", "Ray", "Flash", "Shine", "Gleam",
        "Blade", "Sword", "Shield", "Bow", "Arrow", "Spear", "Axe", "Hammer",
        "Crown", "Throne", "Castle", "Tower", "Fortress", "Temple", "Shrine", "Altar",
        "Scroll", "Book", "Tome", "Grimoire", "Spell", "Charm", "Rune", "Sigil",
        "Potion", "Elixir", "Phial", "Vial", "Flask", "Bottle", "Jar", "Vessel",
        "Key", "Lock", "Chain", "Ring", "Amulet", "Talisman", "Medallion", "Coin",
        "Treasure", "Gold", "Silver", "Bronze", "Platinum", "Jewel", "Gemstone", "Artifact",
        "Relic", "Orb", "Crystal", "Prism", "Lens", "Mirror", "Glass", "Crystal",
        "Flag", "Banner", "Standard", "Pennant", "Emblem", "Symbol", "Sign", "Mark",
        "Path", "Road", "Trail", "Track", "Route", "Way", "Journey", "Quest",
        "Adventure", "Expedition", "Voyage", "Travel", "Wander", "Roam", "Explore", "Discover"
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

