import os

# Before app.config builds Settings: the test client's Host is "testserver", and
# the SDK's DNS-rebinding guard only admits the configured public host.
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["TRACKER_API_URL"] = "http://tracker"
