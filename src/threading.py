import _thread

class Thread:
    def __init__(self, target, args=(), daemon=True):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        _thread.start_new_thread(self.target, self.args)
        
    def is_alive(self):
        return True