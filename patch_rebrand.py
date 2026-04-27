import os
import re

def rebrand_file(filepath):
    if not os.path.exists(filepath):
        print(f"Skipping {filepath}, does not exist.")
        return
        
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Replacements
    # Case sensitive precise replacements
    content = content.replace("Whale Wallets", "Institutional Wallets")
    content = content.replace("Whale Consensus", "Smart Money Consensus")
    content = content.replace("Whale Profiles", "Trader Profiles")
    content = content.replace("Whale Intelligence", "Institutional Intelligence")
    
    # "Whale" to "Block Trader" (Capitalized)
    content = re.sub(r'\bWhale\b', 'Block Trader', content)
    content = re.sub(r'\bWhales\b', 'Block Traders', content)
    
    # "whale" to "block trader" (lowercase, careful not to break image names like whale_logo.png)
    # Only replace if not part of a filename or css class
    # Better: just replace specific phrases
    content = content.replace("whale trades", "block trades")
    content = content.replace("whale alerts", "block alerts")
    content = content.replace("whale moves", "institutional moves")
    
    # "WHALE" -> "BLOCK"
    content = content.replace("WHALE", "BLOCK")
    
    # Alpha
    content = content.replace("Alpha", "Signal")
    content = content.replace("alpha", "signal")
    
    # Mock
    content = content.replace("Mock Portfolio", "Paper Portfolio")
    content = content.replace("Mock Follow", "Paper Trade")
    content = content.replace("Mock portfolio", "Paper portfolio")
    content = content.replace("Mock copy-trade", "Paper trade")

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Rebranded {filepath}")

files = [
    "/Users/adairclark/Desktop/AntiGravity/polyvision_deploy/dashboard/index.html",
    "/Users/adairclark/Desktop/AntiGravity/polyvision_deploy/dashboard/dashboard/index.html",
    "/Users/adairclark/Desktop/AntiGravity/polyvision_deploy/dashboard/app.js",
]

for file in files:
    rebrand_file(file)
