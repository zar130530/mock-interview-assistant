import urllib.request, json, time
from pathlib import Path
BASE="http://127.0.0.1:8033"
PDF=r"D:/桌面/简历.pdf"
data=Path(PDF).read_bytes()
boundary="----xb"
body=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"简历.pdf\"\r\n"
      f"Content-Type: application/pdf\r\n\r\n").encode()+data+f"\r\n--{boundary}--\r\n".encode()
req=urllib.request.Request(BASE+"/api/resume", data=body, method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
t=time.time()
with urllib.request.urlopen(req, timeout=55) as r:
    out=r.read().decode()
print("UPLOAD STATUS", r.status, "elapsed=%.2fs"%(time.time()-t))
print("resp:", out[:200])
# 列表
with urllib.request.urlopen(BASE+"/api/interviews", timeout=10) as r:
    print("list count:", len(json.loads(r.read())))
