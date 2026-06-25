import time

from logger_config import logger

# 固定キーとIV（16バイト）
AES_KEY = b"mDNSProxyPicoKey"
AES_IV  = b"mDNSProxyPico_IV"

def pkcs7_unpad(data):
    """
    PKCS#7 アンパディング
    """
    if not data:
        raise ValueError("Data is empty")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError("Invalid padding length")
    for i in range(len(data) - pad_len, len(data)):
        if data[i] != pad_len:
            raise ValueError("Invalid padding byte")
    return data[:-pad_len]

def decrypt_password(enc_password_b64):
    """
    AES-128-CBC で暗号化され Base64 エンコードされたパスワードを復号する。
    復号に失敗した、または従来の Base64 単体エンコードだった場合は、フォールバックする。
    """
    import binascii
    try:
        # Base64 デコード
        enc_data = binascii.a2b_base64(enc_password_b64)
    except Exception as e:
        logger.warning(f"Failed to base64 decode password: {e}")
        return enc_password_b64

    # まず AES-CBC での復号を試みる
    try:
        import ucryptolib
        # ucryptolib.aes(key, mode, iv) -> mode=2 が CBC モード
        # ※ 実機検証結果: mode=2 が CBC モード
        cipher = ucryptolib.aes(AES_KEY, 2, AES_IV)
        decrypted_padded = cipher.decrypt(enc_data)
        decrypted = pkcs7_unpad(decrypted_padded)
        return decrypted.decode('utf-8')
    except Exception as aes_err:
        logger.info(f"AES decryption failed, falling back to legacy Base64: {aes_err}")
        # フォールバック：従来の単純な Base64 デコードとして処理
        try:
            return enc_data.decode('utf-8')
        except Exception as b64_err:
            logger.warning(f"Legacy Base64 decoding failed: {b64_err}")
            return enc_password_b64

def connect(ssid, password, hostname=None, retries=3, retry_interval=10):
    """
    Raspberry Pi Pico (MicroPython) での Wi-Fi 接続
    """
    try:
        import network
        
        # Wi-Fi接続前にホスト名を設定（OS内蔵mDNS機能の有効化）
        if hostname:
            try:
                network.hostname(hostname)
                logger.info(f"Set network hostname to: {hostname}")
            except Exception as e:
                logger.warning(f"Could not set hostname: {e}")
                
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)

        # 省電力モードの無効化（Wi-Fi接続の安定化・数秒での切断対策）
        try:
            # 0xa11140 = PM_NONE (MicroPythonのWi-Fi省電力を完全にオフにする数値)
            wlan.config(pm=0xa11140)
            logger.info("Disabled Wi-Fi power management (set pm=0xa11140)")
        except Exception as e:
            try:
                if hasattr(network.WLAN, 'PM_NONE'):
                    wlan.config(pm=network.WLAN.PM_NONE)
                    logger.info("Disabled Wi-Fi power management via PM_NONE")
            except Exception:
                logger.warning(f"Could not disable power management: {e}")
                
        time.sleep(1) # wlan active後の安定化待ち

        try:
            import machine
            # Wi-Fiチップがactiveになった後にのみLEDを触る
            led = machine.Pin("LED", machine.Pin.OUT)
            # 起動確認のために少しだけ点滅させる
            for _ in range(6):
                led.toggle()
                time.sleep(0.1)
            led.value(0)
        except Exception:
            led = None

        decoded_password = decrypt_password(password)

        for attempt in range(1, retries + 1):
            if wlan.isconnected():
                break
                
            logger.info(f"Connecting to network '{ssid}' (Attempt {attempt}/{retries})...")
            try:
                wlan.disconnect() # 前回の試行をクリア
                time.sleep(1)
            except Exception:
                pass
                
            wlan.connect(ssid, decoded_password)
            
            # 接続待ちタイムアウト（15秒）
            timeout = 15
            while not wlan.isconnected() and timeout > 0:
                if led:
                    led.toggle()
                time.sleep(0.5)
                timeout -= 0.5

            if wlan.isconnected():
                break
                
            logger.warning(f"Attempt {attempt} failed. Waiting {retry_interval} seconds before retrying...")
            if led:
                led.value(0)
            time.sleep(retry_interval)
        
        if wlan.isconnected():
            if led:
                led.value(1)
            logger.info(f"Network connected: {wlan.ifconfig()}")
            return True
        else:
            if led:
                led.value(0)
            logger.error("Failed to connect to network after multiple attempts.")
            return False
    except ImportError:
        logger.warning("network module not found. This feature is only for MicroPython.")
        return False
    except Exception as e:
        logger.error(f"Wi-Fi connection error: {e}")
        return False