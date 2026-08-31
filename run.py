from __future__ import annotations

import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "jobagent.main:app",
        host=os.getenv("JOBAGENT_HOST", "127.0.0.1"),
        port=int(os.getenv("JOBAGENT_PORT", "8765")),
        reload=False,
    )
