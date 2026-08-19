import uuid
from oncall.application.incident_service import incident_fingerprint

def test_fingerprint_stable():
    p=uuid.uuid4();r=uuid.uuid4();a=incident_fingerprint(p,r,'host','cpu');b=incident_fingerprint(p,r,'host','cpu');assert a==b and len(a)==64
