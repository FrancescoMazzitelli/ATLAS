from flask import Flask
from flask_restx import Api
from controller.routingController import api as traffic_ns
from cheroot.wsgi import Server

app = Flask(__name__)
app.config['BUNDLE_ERRORS'] = True

api = Api(
    app,
    version="1.0",
    title="Routing Updater API",
    description="API for updating traffic status on the Valhalla routing engine"
)

api.add_namespace(traffic_ns, path="/traffic")

if __name__ == "__main__":
    server = Server(("0.0.0.0", 7500), app)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()