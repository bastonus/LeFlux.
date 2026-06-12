import urllib.request
import hashlib

def get_info_hash(torrent_data):
    def decode(data, index):
        c = data[index]
        if c == ord('i'):
            end = data.find(b'e', index)
            return None, end + 1
        elif c == ord('l'):
            index += 1
            while data[index] != ord('e'):
                _, index = decode(data, index)
            return None, index + 1
        elif c == ord('d'):
            index += 1
            info_start = info_end = None
            while data[index] != ord('e'):
                key, index = decode(data, index)
                val_start = index
                _, index = decode(data, index)
                if key == b'info':
                    info_start = val_start
                    info_end = index
            return (info_start, info_end), index + 1
        elif ord('0') <= c <= ord('9'):
            colon = data.find(b':', index)
            length = int(data[index:colon])
            start = colon + 1
            end = start + length
            return data[start:end], end
        else:
            raise ValueError(f"Invalid bencode at {index}")
            
    try:
        (info_start, info_end), _ = decode(torrent_data, 0)
        if info_start is not None and info_end is not None:
            return hashlib.sha1(torrent_data[info_start:info_end]).hexdigest()
    except Exception as e:
        print("Error:", e)
    return None

url = "https://gemini-tracker.org/torrent/download/1793.e1b6f9fca2955f25135a31bf0c7bc63c"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req) as response:
    data = response.read()

print("Info hash:", get_info_hash(data))
