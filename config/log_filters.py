import logging,re


class SubscriptionTokenFilter(logging.Filter):
    def filter(self,record):
        pattern=r'(/work/calendar/subscription/)[^\s/?]+\.ics'
        def clean(value):
            if not isinstance(value,str):return value
            value=re.sub(pattern,r'\1[redacted].ics',value)
            return re.sub(r'(/work/meetings/microsoft/callback/)\?[^\s"\']+',r'\1?[redacted]',value)
        record.msg=clean(record.msg)
        if isinstance(record.args,tuple):record.args=tuple(clean(value) for value in record.args)
        elif isinstance(record.args,dict):record.args={key:clean(value) for key,value in record.args.items()}
        return True
