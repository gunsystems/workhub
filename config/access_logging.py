from gunicorn.glogging import Logger


class WorkhubLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms=super().atoms(resp,req,environ,request_time)
        if (environ.get('PATH_INFO') or '').startswith('/work/calendar/subscription/'):
            atoms['U']='/work/calendar/subscription/[redacted]'
            atoms['r']='calendar subscription [redacted]'
            atoms['q']=''
        return atoms
