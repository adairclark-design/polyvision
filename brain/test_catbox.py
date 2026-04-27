import requests
from pathlib import Path

def test_catbox(file_path):
    print(f"Testing catbox upload for {file_path}")
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload"},
                files={"fileToUpload": (Path(file_path).name, f)},
                timeout=60,
            )
        print("Status code:", resp.status_code)
        print("Response text:", resp.text)
    except Exception as e:
        print("Exception:", e)

test_catbox('/Users/adairclark/Desktop/AntiGravity/polyvision_deploy/brain/assets/logo_fallback.png')
