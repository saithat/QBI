# Workers

Worker process entry points live here. The hackathon extraction internals remain behind the stable,
versioned `workers/extraction` boundary. `workers/jobs` owns finite scientific operations, the
long-lived generic job worker, and the Kubernetes acceptance client. `workers/workflow` owns the
external Temporal worker process; scientific workflow definitions beyond the runtime probe remain
deferred.

`workers/discovery` owns the bounded official-source CLI used by the Kubernetes CronJob. It emits
canonical discovery batches into PostgreSQL and never downloads article bytes in PRD-015.
