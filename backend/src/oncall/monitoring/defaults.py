DEFAULT_RULES = [
    dict(metric_key='host.cpu.percent',operator='>',trigger_threshold=85,trigger_for=2,recovery_threshold=70,recovery_for=2,severity='warning'),
    dict(metric_key='host.memory.percent',operator='>',trigger_threshold=85,trigger_for=2,recovery_threshold=75,recovery_for=2,severity='warning'),
    dict(metric_key='host.disk.usage_percent',operator='>',trigger_threshold=85,trigger_for=1,recovery_threshold=80,recovery_for=1,severity='warning'),
    dict(metric_key='service.reachable',operator='<',trigger_threshold=1,trigger_for=2,recovery_threshold=0.5,recovery_for=2,severity='critical'),
]
