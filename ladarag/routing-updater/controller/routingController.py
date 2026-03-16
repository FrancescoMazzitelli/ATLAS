from flask_restx import Namespace, Resource, fields
from flask import request
from service.routingService import TrafficService

api = Namespace("traffic", description="Traffic tar operations")

patch_model = api.model("PatchRequest", {
    "tar_path": fields.String(required=True),
    "edge_ids": fields.List(fields.Integer, required=True),
    "speed": fields.Integer(required=True)
})

reset_model = api.model("ResetRequest", {
    "tar_path": fields.String(required=True),
    "edge_ids": fields.List(fields.Integer, required=True)
})

inspect_model = api.model("InspectRequest", {
    "tar_path": fields.String(required=True),
    "edge_ids": fields.List(fields.Integer, required=True)
})

pipeline_model = api.model("PipelineRequest", {
    "edge_ids": fields.List(fields.Integer, required=True),
    "speed": fields.Integer(required=True)
})


@api.route("/build-patch-restart")
class BuildPatchRestart(Resource):

    @api.expect(pipeline_model)
    def post(self):

        data = request.json

        result = TrafficService.build_patch_restart(
            data["edge_ids"],
            data["speed"]
        )

        return result
    
@api.route("/patch")
class Patch(Resource):

    @api.expect(patch_model)
    def post(self):
        data = request.json
        result = TrafficService.patch(
            data["tar_path"],
            data["edge_ids"],
            data["speed"]
        )
        return {"status": "ok", "result": result}


@api.route("/reset")
class Reset(Resource):

    @api.expect(reset_model)
    def post(self):
        data = request.json
        result = TrafficService.reset(
            data["tar_path"],
            data["edge_ids"]
        )
        return {"status": "ok", "result": result}


@api.route("/inspect")
class Inspect(Resource):

    @api.expect(inspect_model)
    def post(self):
        data = request.json
        result = TrafficService.inspect(
            data["tar_path"],
            data["edge_ids"]
        )
        return {"status": "ok", "edges": result}