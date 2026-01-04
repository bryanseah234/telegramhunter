from PIL import Image, ImageDraw, ImageFont

def create_icon(size, filename):
    # Fofa green color approx
    bg_color = (19, 174, 103)
    img = Image.new('RGB', (size, size), color = bg_color)
    d = ImageDraw.Draw(img)
    
    # Simple "F" logic - centering text is tricky with default font, drawing rects is safer
    # or just a simple Text if we had a font. Let's draw a stylized "F" or just "F"
    
    # Draw simple "F" using rectangles for robustness without external fonts
    padding = size // 5
    thickness = size // 5
    
    # Vertical bar
    d.rectangle([padding, padding, padding + thickness, size - padding], fill=(255, 255, 255))
    
    # Top horizontal
    d.rectangle([padding, padding, size - padding, padding + thickness], fill=(255, 255, 255))
    
    # Middle horizontal
    mid_y = size // 2
    d.rectangle([padding, mid_y, size - padding - thickness, mid_y + thickness], fill=(255, 255, 255))

    img.save(filename)

create_icon(16, "c:/telegramhunter/chrome_extension/icons/icon16.png")
create_icon(48, "c:/telegramhunter/chrome_extension/icons/icon48.png")
create_icon(128, "c:/telegramhunter/chrome_extension/icons/icon128.png")
print("Fofa icons created successfully.")
