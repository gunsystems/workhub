import logging,re


class SubscriptionTokenFilter(logging.Filter):
    def filter(self,record):
        pattern=r'(/work/calendar/subscription/)[^\s/?]+\.ics'
        def clean(value):return re.sub(pattern,r'\1[redacted].ics',value) if isinstance(value,str) else value
        record.msg=clean(record.msg)
        if isinstance(record.args,tuple):record.args=tuple(clean(value) for value in record.args)
        elif isinstance(record.args,dict):record.args={key:clean(value) for key,value in record.args.items()}
        return True
