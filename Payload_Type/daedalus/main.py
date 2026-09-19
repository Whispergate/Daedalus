import asyncio
from pathlib import Path
from dotenv import load_dotenv

env_file = Path("/Mythic/.env")
if env_file.exists():
    load_dotenv(env_file, override=False)

from mythic_container import mythic_service
from daedalus.eventing import DaedalusEventing  # noqa: F401 - registers with mythic_service
from daedalus_ca.builder import DaedalusCA  # noqa: F401 - registers CA with mythic_service

asyncio.run(mythic_service.start_and_run_forever())
