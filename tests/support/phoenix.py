import copy
from types import SimpleNamespace

import httpx


class PhoenixStub:
    def __init__(self):
        self.datasets = self
        self.experiments = self
        self.saved_datasets = {}
        self.created_experiments = []
        self.runs = []
        self.evaluations = []
        self.unavailable = False
        self.fail_recording = False
        self.run_attempts = 0

    async def get_dataset(self, *, dataset, **kwargs):
        if isinstance(dataset, dict):
            dataset = dataset["name"]
        if self.unavailable:
            raise httpx.ConnectError("fixture unavailable")
        if dataset not in self.saved_datasets:
            request = httpx.Request("GET", "http://127.0.0.1:6006/v1/datasets")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return self.saved_datasets[dataset]

    async def list(self, **kwargs):
        if self.unavailable:
            raise httpx.ConnectError("fixture unavailable")
        return [
            {"name": name, "id": dataset.id}
            for name, dataset in self.saved_datasets.items()
        ]

    async def create_dataset(self, *, name, inputs, outputs, metadata, **kwargs):
        dataset = SimpleNamespace(
            id=f"dataset-{len(self.saved_datasets)}",
            version_id=f"version-{len(self.saved_datasets)}",
            examples=[
                {
                    "id": f"row-{i}",
                    "node_id": f"node-{i}",
                    "input": copy.deepcopy(a),
                    "output": copy.deepcopy(b),
                    "metadata": copy.deepcopy(c),
                }
                for i, (a, b, c) in enumerate(
                    zip(inputs, outputs, metadata, strict=True)
                )
            ],
        )
        self.saved_datasets[name] = dataset
        return dataset

    async def create(self, **kwargs):
        value = {**kwargs, "id": f"experiment-{len(self.created_experiments)}"}
        self.created_experiments.append(value)
        return value

    async def log_run(self, **kwargs):
        self.run_attempts += 1
        if self.fail_recording:
            raise httpx.ConnectError("fixture recording failed")
        value = {**kwargs, "id": f"run-{len(self.runs)}"}
        self.runs.append(value)
        return value

    async def log_evaluation(self, **kwargs):
        self.evaluations.append(kwargs)
        return {"id": str(len(self.evaluations))}
