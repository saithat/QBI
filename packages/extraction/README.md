# HiveBlot extraction package

`hiveblot_extraction` owns western-blot scientific pipeline orchestration. It reads verified
immutable artifacts, invokes configurable detector/model adapters, normalizes permissive external
output into strict contracts, assembles reviewable structured and spatial predictions, and records
the work through evaluation and pipeline-registry services.

It does not own HTTP schemas, ORM entities, artifact publication, worker leases, container
execution, reviewer corrections, or densitometry. The retained OpenCV/Qwen adapter is one
implementation of the package protocol rather than the package's domain interface.
