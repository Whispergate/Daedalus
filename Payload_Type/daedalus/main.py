import asyncio
from mythic_container import mythic_service
from daedalus.eventing import DaedalusEventing  # noqa: F401 - registers with mythic_service
from Payload_Type.daedalus.daedalus_ca.builder import DaedalusCA  # noqa: F401 - registers CA with mythic_service

asyncio.run(mythic_service.start_and_run_forever())
