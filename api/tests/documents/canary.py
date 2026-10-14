"""The canary of the documents module's tests (D2), the mail module's imported as it is.

Content, profile values, posting texts, chat messages and corrections never reach a log, an
error, an arq argument or an arq result (SPEC-documents.md, "Privacy"). A test plants a marker
(`marker()`: a random token, never a common word) in a field, runs the code, then asks
`find(markers, logs=..., redis=..., values=...)` where it ended up: an empty answer is the pass.
`test_canary.py` proves that a planted marker IS found, so that an empty answer means something.
"""

from tests.mail.canary import Finding, find, marker, record_text

__all__ = ["Finding", "find", "marker", "record_text"]
