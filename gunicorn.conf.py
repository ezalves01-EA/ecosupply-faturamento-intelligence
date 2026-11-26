worker_class = 'gthread'
threads = 12
timeout = 180
graceful_timeout = 180


def post_worker_init(worker):
    from app import engine,FIELDS
    from sisgr_annual import launch
    launch(engine,FIELDS)
