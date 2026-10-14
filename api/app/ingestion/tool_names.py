"""The names of the tools of the ingestion agents, apart from the tools themselves (tools.py):
the api registers the agents' definitions (agents.py) and its image holds no Claude Agent SDK,
which tools.py needs to build a tool."""

READ_DOCUMENT = "read_document"
PROPOSE = "propose_profile_changes"
VIEW_IMAGE = "view_image"
STORE_TRANSCRIPTION = "store_transcription"
