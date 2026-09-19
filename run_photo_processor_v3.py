from __future__ import annotations

import run_photo_processor_v2 as q
from app.quality_brand_v3 import brand_photo_bytes

q.brand_photo_bytes = brand_photo_bytes
q.PROCESSOR_VERSION = "filinkov-quality-compositor-v3"
q.log.name = "photo-processor-v3"

if __name__ == "__main__":
    q.main()
