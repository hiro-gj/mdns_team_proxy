class _GeneratorContextManager:
    def __init__(self, func, args, kwds):
        self.gen = func(*args, **kwds)

    def __enter__(self):
        try:
            return next(self.gen)
        except StopIteration:
            raise RuntimeError("generator didn't yield")

    def __exit__(self, type, value, traceback):
        if type is None:
            try:
                next(self.gen)
            except StopIteration:
                return False
            else:
                raise RuntimeError("generator didn't stop")
        else:
            try:
                self.gen.throw(type, value, traceback)
                return False
            except StopIteration:
                return True
            except Exception as e:
                if e is value:
                    return False
                raise

def contextmanager(func):
    def helper(*args, **kwds):
        return _GeneratorContextManager(func, args, kwds)
    return helper