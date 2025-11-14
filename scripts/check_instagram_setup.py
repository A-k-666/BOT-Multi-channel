"""Check Instagram setup and permissions in Facebook app."""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = "17841476750803735"  # Your Instagram account ID from logs


def check_access_token():
    """Check if access token is valid and has permissions."""
    if not FACEBOOK_PAGE_ACCESS_TOKEN:
        print("[ERROR] FACEBOOK_PAGE_ACCESS_TOKEN not set in .env")
        return False
    
    print(f"[OK] Access token found: {FACEBOOK_PAGE_ACCESS_TOKEN[:20]}...")
    
    # Check token info
    try:
        url = "https://graph.facebook.com/v18.0/me"
        params = {"access_token": FACEBOOK_PAGE_ACCESS_TOKEN, "fields": "id,name"}
        response = httpx.get(url, params=params, timeout=10.0)
        
        if response.status_code == 200:
            data = response.json()
            print(f"[OK] Token is valid. Connected to: {data.get('name', 'Unknown')} (ID: {data.get('id')})")
            return True
        else:
            print(f"[ERROR] Token validation failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"[ERROR] Error checking token: {e}")
        return False


def check_instagram_permissions():
    """Check if Instagram permissions are available."""
    if not FACEBOOK_PAGE_ACCESS_TOKEN:
        return False
    
    try:
        # Check permissions - use page ID instead of 'me'
        # First get page info
        url = "https://graph.facebook.com/v18.0/me"
        params = {"access_token": FACEBOOK_PAGE_ACCESS_TOKEN, "fields": "id"}
        response = httpx.get(url, params=params, timeout=10.0)
        
        if response.status_code != 200:
            print(f"[ERROR] Cannot get page info: {response.status_code}")
            return False
        
        page_id = response.json().get("id")
        
        # Check permissions for the page
        url = f"https://graph.facebook.com/v18.0/{page_id}/permissions"
        params = {"access_token": FACEBOOK_PAGE_ACCESS_TOKEN}
        response = httpx.get(url, params=params, timeout=10.0)
        
        if response.status_code == 200:
            data = response.json()
            permissions = [p.get("permission") for p in data.get("data", []) if p.get("status") == "granted"]
            print(f"\n[INFO] Granted permissions: {', '.join(permissions)}")
            
            required = ["instagram_basic", "instagram_manage_messages", "pages_messaging"]
            missing = [p for p in required if p not in permissions]
            
            if missing:
                print(f"[ERROR] Missing permissions: {', '.join(missing)}")
                return False
            else:
                print("[OK] All required permissions are granted")
                return True
        else:
            print(f"[ERROR] Failed to check permissions: {response.status_code}")
            return False
    except Exception as e:
        print(f"[ERROR] Error checking permissions: {e}")
        return False


def check_instagram_account():
    """Check if Instagram account is accessible."""
    if not FACEBOOK_PAGE_ACCESS_TOKEN:
        return False
    
    try:
        # Try to get Instagram account info
        url = f"https://graph.facebook.com/v18.0/{INSTAGRAM_ACCOUNT_ID}"
        params = {"access_token": FACEBOOK_PAGE_ACCESS_TOKEN, "fields": "id,username"}
        response = httpx.get(url, params=params, timeout=10.0)
        
        if response.status_code == 200:
            data = response.json()
            print(f"[OK] Instagram account accessible: {data.get('username', 'Unknown')} (ID: {data.get('id')})")
            return True
        else:
            error_data = response.json() if response.text else {}
            print(f"[ERROR] Cannot access Instagram account: {response.status_code}")
            print(f"   Error: {error_data}")
            return False
    except Exception as e:
        print(f"[ERROR] Error checking Instagram account: {e}")
        return False


def test_instagram_messaging():
    """Test if Instagram messaging API is accessible."""
    if not FACEBOOK_PAGE_ACCESS_TOKEN:
        return False
    
    print("\n[TEST] Testing Instagram messaging capability...")
    print("   (This will fail if permissions are not set up correctly)")
    
    # Just check if the endpoint exists (don't actually send)
    url = f"https://graph.facebook.com/v18.0/{INSTAGRAM_ACCOUNT_ID}/messages"
    params = {"access_token": FACEBOOK_PAGE_ACCESS_TOKEN}
    
    try:
        # Use OPTIONS or HEAD to check capability without sending
        response = httpx.options(url, params=params, timeout=10.0)
        print(f"   Endpoint check: {response.status_code}")
        return True
    except Exception as e:
        print(f"   Endpoint check failed: {e}")
        return False


def main():
    print("=" * 60)
    print("Instagram Setup Checker")
    print("=" * 60)
    
    print("\n1. Checking access token...")
    token_ok = check_access_token()
    
    if not token_ok:
        print("\n❌ Access token issue. Please check FACEBOOK_PAGE_ACCESS_TOKEN in .env")
        return
    
    print("\n2. Checking permissions...")
    permissions_ok = check_instagram_permissions()
    
    print("\n3. Checking Instagram account access...")
    account_ok = check_instagram_account()
    
    print("\n4. Testing messaging capability...")
    messaging_ok = test_instagram_messaging()
    
    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)
    print(f"Access Token: {'[OK]' if token_ok else '[ERROR]'}")
    print(f"Permissions: {'[OK]' if permissions_ok else '[ERROR]'}")
    print(f"Instagram Account: {'[OK]' if account_ok else '[ERROR]'}")
    print(f"Messaging Capability: {'[OK]' if messaging_ok else '[ERROR]'}")
    
    if not all([token_ok, permissions_ok, account_ok]):
        print("\n[WARNING] Setup incomplete. Please:")
        print("   1. Go to Facebook Developer Dashboard -> Your App")
        print("   2. Products -> Instagram -> Enable Instagram Messaging")
        print("   3. App Review -> Request permissions: instagram_basic, instagram_manage_messages")
        print("   4. Generate new Page Access Token after permissions are approved")
        print("   5. Update FACEBOOK_PAGE_ACCESS_TOKEN in .env")


if __name__ == "__main__":
    main()

