# Gunicorn loads this file from the project root. No extra paid service is needed.
access_log_format = '%(h)s %(m)s %(U)s %(s)s %(L)s'
logger_class = 'config.access_logging.WorkhubLogger'


def post_worker_init(worker):
    from tasks.scheduler import start
    start()


def worker_exit(server,worker):
    from tasks.scheduler import stop
    stop()
