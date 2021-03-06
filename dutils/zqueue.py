from dutils.zutils import ZReplier, query_maker, send_multi, ZNull, process_command
import threading, time, Queue, bsddb, json

ZQUEQUE_BIND = "tcp://127.0.0.1:7575"
DBFILE = "./zqueue.bdb"
DURATION = 5

def log(msg):
    print "[%s]: %s" % (time.asctime(), msg)

# BDBPersistentQueue # {{{
class BDBPersistentQueue(object):
    # initalizations # {{{
    def __init__(self, db, namespace):
        log("BDBPersistentQueue.__init__:%s" % namespace)
        self.db = db
        self.namespace = namespace
        self.init_and_check_db()

    def init_and_check_db(self):
        log("BDBPersistentQueue.init_and_check_db:%s" % self.namespace)
        if not self.has_key("initialized"):
            self.initialize_db()
        self.seen = self.bottom
        for i in range(self.bottom + 1, self.top + 1):
            if self.has_key(i): self.mark_unassigned(i)
        print "BDBPersistentQueue opened queue: %s with b/s/t: %s/%s/%s" % (
            self.namespace, self.bottom, self.seen, self.top
        )
        assert self.bottom <= self.top

    def initialize_db(self):
        log("BDBPersistentQueue.initialize_db:%s" % self.namespace)
        self.set("top", 0)
        self.set("bottom", 0)
        self.set("seen", 0)
        self.set("initialized", "True")
        self.set("initialized_on", time.asctime())
    # }}}

    # assigning tasks # {{{
    def mark_assigned(self, item_id):
        self.set(item_id, "True,%s" % self.get(item_id).split(",", 1)[1])

    def mark_unassigned(self, item_id):
        self.set(item_id, "False,%s" % self.get(item_id).split(",", 1)[1])

    def is_assigned(self, item_id):
        return self.get(item_id).split(",", 1)[0] == "True"
    # }}}

    # properties # {{{
    def get_top(self): return int(self.get("top"))
    def set_top(self, v): self.set("top", str(v))
    def get_bottom(self): return int(self.get("bottom"))
    def set_bottom(self, v): self.set("bottom", str(v))
    def get_seen(self): return int(self.get("seen"))
    def set_seen(self, v): self.set("seen", str(v))

    top = property(get_top, set_top)
    bottom = property(get_bottom, set_bottom)
    seen = property(get_seen, set_seen)
    # }}}

    # namespace helpers # {{{
    def has_key(self, key):
        return "%s:%s" % (self.namespace, key) in self.db

    def get(self, key):
        return self.db["%s:%s" % (self.namespace, key)]

    def set(self, key, value):
        self.db["%s:%s" % (self.namespace, key)] = str(value)

    def del_key(self, key):
        del self.db["%s:%s" % (self.namespace, key)]
    # }}}

    # core methods # {{{
    # add # {{{
    def add(self, item):
        next_id = self.top = self.top + 1
        self.set(next_id, "False,%s" % item)
        log("BDBPersistentQueue.add:%s:%s" % (next_id, item))
        #self.db.sync()
        return next_id
    # }}}

    # pop_item # {{{
    def pop_item(self):
        if self.is_empty(): return None, None
        # there is something with us. increment seen
        current_seen = self.seen
        current_top = self.top
        while current_seen <= current_top:
            current_seen += 1
            if (
                self.has_key(current_seen) and
                not self.is_assigned(current_seen)
            ): break
        self.seen = current_seen
        self.mark_assigned(current_seen)
        #self.db.sync()
        return str(current_seen), self.get(current_seen).split(",", 1)[1]
    # }}}

    # is_empty {{{
    def is_empty(self): return self.seen == self.top
    # }}}

    # delete {{{
    def delete(self, item_id):
        log("BDBPersistentQueue.delete:%s" % item_id)
        self.del_key(item_id)
        item_id = int(item_id)
        bottom = self.bottom
        if item_id != bottom + 1: return
        # we just deleted the bottom item... update self.bottom and self.seen
        # new self.bottom should be first element going up that is not empty
        top, seen = self.top, self.seen
        while item_id <= top:
            item_id += 1
            if self.has_key(item_id): break
        self.bottom = bottom = item_id - 1
        if seen < bottom: self.seen = bottom
    # }}}

    # reset {{{
    def reset(self, item_id):
        item_id = int(item_id)
        assert item_id >= self.bottom
        self.mark_unassigned(item_id)
        if item_id <= self.seen: self.seen = item_id - 1
        #self.db.sync()
    # }}}
    # }}}
# }}}

# GettersQueue # {{{
class GettersQueue(object):
    def __init__(self, namespace):
        self.namespace = namespace
        self.q = Queue.Queue()

    def pop_getter(self): 
        getter = self.q.get()
        self.q.task_done()
        return getter

    def is_empty(self): return self.q.empty()
    def add(self, getter): self.q.put(getter)
# }}}

# NamespacedQueue # {{{
class NamespacedQueue(object):
    def __init__(self, db, namespace):
        self.namespace = namespace
        self.pq = BDBPersistentQueue(db, namespace)
        self.gq = GettersQueue(namespace)
# }}}

# Resetter # {{{
class Resetter(threading.Thread):
    def __init__(self):
        super(Resetter, self).__init__()
        self.items_to_reset = Queue.Queue()

    def enque(self, item):
        self.items_to_reset.put(item)

    def shutdown(self):
        self.enque("Resetter.Shutdown")

    def run(self):
        reset_query = query_maker(bind=ZQUEQUE_BIND)
        while True:
            item = self.items_to_reset.get()
            self.items_to_reset.task_done()
            if item == "Resetter.Shutdown": 
                log("Resetter.run: shutting down")
                break
            log("Resetter.run: resetting %s" % item)
            reset_query(item)
# }}}

# DelayedResetter # {{{
class DelayedResetter(threading.Thread):
    def __init__(self, item_id, requester, resetter):
        super(DelayedResetter, self).__init__()
        self.item_id = item_id
        self.requester = requester
        self.ignore_it = threading.Event()
        self.resetter = resetter

    def run(self):
        self.ignore_it.wait(DURATION)
        if self.ignore_it.isSet(): return
        self.resetter.enque(self.item_id)
# }}}

# Single Threaded QueueManager # {{{
class QueueManager(object):
    def __init__(self, socket):
        self.qs = {}
        self.socket = socket
        self.assigned_items = {}
        self.db = bsddb.hashopen(DBFILE)
        self.resetter = Resetter()
        self.resetter.start()

    def get_q(self, namespace):
        if namespace not in self.qs:
            self.qs[namespace] = NamespacedQueue(self.db, namespace)
        return self.qs[namespace]

    def assign_item(self, namespace, item_id, item, requester):
        send_multi(self.socket, [requester, ZNull, item_id + ":" + item])
        key = "%s:reset:%s" % (namespace, item_id)
        self.assigned_items[key] = DelayedResetter(key, requester, self.resetter)
        self.assigned_items[key].start()

    def assign_next_if_possible(self, namespace, q):
        if q.pq.is_empty() or q.gq.is_empty(): return
        item_id, item = q.pq.pop_item()
        requester = q.gq.pop_getter()
        self.assign_item(namespace, item_id, item, requester)

    def handle_get(self, namespace, sender, blocking=True):
        q = self.get_q(namespace)
        if q.pq.is_empty():
            if blocking:
                q.gq.add(sender)
            else:
                send_multi(self.socket, [sender, ZNull, "ZQueue.Empty"])
        else:
            item_id, item = q.pq.pop_item()
            self.assign_item(namespace, item_id, item, sender)

    def handle_delete(self, namespace, item_id):
        q = self.get_q(namespace)
        q.pq.delete(item_id)
        key = "%s:reset:%s" % (namespace, item_id)
        if key in self.assigned_items:
            self.assigned_items[key].ignore_it.set()
            del self.assigned_items[key]

    def handle_add(self, namespace, item):
        if type(item) == type({}): item = json.dumps(item)
        q = self.get_q(namespace)
        item_id = q.pq.add(item)
        self.assign_next_if_possible(namespace, q)
        return item_id

    def handle_reset(self, namespace, item_id):
        q = self.get_q(namespace)
        key = "%s:reset:%s" % (namespace, item_id)
        del self.assigned_items[key]
        if q.pq.has_key(item_id): q.pq.reset(item_id)
        self.assign_next_if_possible(namespace, q)
# }}}

# ZQueue # {{{
class ZQueue(ZReplier):
    def thread_init(self):
        super(ZQueue, self).thread_init()
        self.qm = QueueManager(self.socket)

    def xreply(self, sender, message):
        arguments = process_command(message)
        if len(arguments) == 2: arguments.append("")
        try:
            namespace, command, argument = arguments
        except ValueError:
            return send_multi(
                self.socket, [sender, ZNull, super(ZQueue, self).reply(message)]
            )
        #print namespace, command
        if command == "get":
            self.qm.handle_get(namespace, sender)
        elif command == "nbget":
            self.qm.handle_get(namespace, sender, blocking=False)
        elif command.startswith("delete"):
            self.qm.handle_delete(namespace, argument)
            send_multi(self.socket, [sender, ZNull, "ack"])
        elif command.startswith("add"):
            item_id = self.qm.handle_add(namespace, argument)
            send_multi(self.socket, [sender, ZNull, str(item_id)])
        elif command.startswith("reset"):
            self.qm.handle_reset(namespace, argument)
            send_multi(self.socket, [sender, ZNull, "ack"])
        else:
            log("Unknown command: %s" % command)
            send_multi(self.socket, [sender, ZNull, "Unknown command: %s" % command])

    def thread_quit(self):
        for namespaced_queue in self.qm.qs.values():
            while not namespaced_queue.gq.is_empty():
                send_multi(
                    self.socket,
                    [namespaced_queue.gq.pop_getter(), ZNull, "ZQueue.Shutdown"]
                )
        self.qm.resetter.shutdown()
        super(ZQueue, self).thread_quit()
# }}}

query = query_maker(bind=ZQUEQUE_BIND)

# ZQueueConsumer # {{{
class ZQueueConsumer(threading.Thread):
    def __init__(self, bind, namespace):
        super(ZQueueConsumer, self).__init__()
        self.daemon = True

        self.namespace = namespace
        self.bind = bind
        self.start()
        print "ZQueueConsumer for", namespace

    def process(self, item): pass

    def run(self):
        q = query_maker(bind=self.bind)
        while True:
            msg = q("%s:get" % self.namespace)
            print "ZQueueConsumer:run", msg
            if msg == "ZQueue.Shutdown": continue
            item_id, item = msg
            self.process(item)
            q("%s:delete:%s" % (self.namespace, item_id))
# }}}

# ZQueueMultiConsumer # {{{
class ZQueueMultiConsumer(threading.Thread):
    def __init__(self, *queues):
        self.queues = queues
        self.task_queue = Queue.Queue()

    def process(self, item, namespace, bind): pass

    def run(self):
        while True:
            msg = self.task_queue.get()
            if msg == "ZQueueMultiConsumer.Shutdown":
                break
            item, namespace, bind = msg
            self.process(item, namespace, bind)

    def shutdown(self):
        self.task_queue.put("ZQueueMultiConsumer.Shutdown")

    def loop(self):
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print "Terminating ZQueueMultiConsumer..."
            self.shutdown()
            self.join()
            print "Terminated."

# }}}


def main():
    ZQueue(bind=ZQUEQUE_BIND).loop()

if __name__ == "__main__":
    main()
