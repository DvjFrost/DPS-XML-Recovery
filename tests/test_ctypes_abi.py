import ctypes

from iit_eusign_ctypes import EUSignCP, IITError


class FakeFunction:
    def __init__(self, result=None):
        self.result = result
        self.calls = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class FakeDLL:
    def __init__(self, initialize_result=0):
        self._handle = 123
        self.EUInitialize = FakeFunction(initialize_result)
        self.EUIsInitialized = FakeFunction(1)
        self.EUFinalize = FakeFunction(None)


def test_binds_documented_abi_prototypes():
    dll = FakeDLL()
    api = EUSignCP(loader=lambda _: dll, unloader=lambda _: None)

    assert dll.EUInitialize.argtypes == []
    assert dll.EUInitialize.restype is ctypes.c_uint32
    assert dll.EUIsInitialized.argtypes == []
    assert dll.EUIsInitialized.restype is ctypes.c_int32
    assert dll.EUFinalize.argtypes == []
    assert dll.EUFinalize.restype is None
    api.close()


def test_context_initializes_finalizes_and_unloads_in_order():
    events = []
    dll = FakeDLL()
    dll.EUInitialize = FakeFunction(0)
    dll.EUInitialize.__call__ = lambda: events.append("initialize") or 0
    original_initialize = dll.EUInitialize

    def initialize():
        events.append("initialize")
        return 0

    def finalize():
        events.append("finalize")

    original_initialize.__call__ = initialize
    dll.EUInitialize = initialize
    dll.EUIsInitialized = FakeFunction(1)
    dll.EUFinalize = finalize

    with EUSignCP(loader=lambda _: dll, unloader=lambda _: events.append("unload")) as api:
        events.append("body")
        assert api.is_initialized()

    assert events == ["initialize", "body", "finalize", "unload"]


def test_initialize_failure_unloads_without_finalize():
    events = []
    dll = FakeDLL(initialize_result=7)

    try:
        with EUSignCP(loader=lambda _: dll, unloader=lambda _: events.append("unload")):
            raise AssertionError("unreachable")
    except IITError as exc:
        assert exc.code == 7
    else:
        raise AssertionError("IITError was not raised")

    assert dll.EUFinalize.calls == []
    assert events == ["unload"]

