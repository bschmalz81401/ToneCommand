"""HeadRush device support (#33).

Phase 1 is `client.py`: transport only. It knows HTTP, mDNS and the shape of
the unit's own API, and nothing about ToneCommand. The `DeviceAdapter`
implementation, the capability declaration and the routing table are later
phases and deliberately absent here.
"""
