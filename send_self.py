# Based on https://gist.github.com/Varriount/aba020b9d43c13d2794b

import weakref
from functools import wraps, partial

import sys
import time
import threading

import sublime_plugin
import sublime


class GeneratorWrapperBase(object):

    """TODOC
    """

    def __init__(self, catch_stopiteration=True, debug=False):
        self.catch_stopiteration = catch_stopiteration
        self.debug = debug

        if self.debug:
            print("new Wrapper created", self)

    def __del__(self):
        if self.debug:
            print("Wrapper is being deleted", self)

    generator = NotImplemented

    weak_generator = NotImplemented

    def with_strong_ref(self):
        return NotImplemented

    def with_weak_ref(self):
        return NotImplemented

    @property
    def send(self):
        return partial(self._send, self.generator)

    # A wrapper around send with a default value
    def _send(self, generator, value=None):
        if self.debug:
            print("send:", generator, value)
        if self.catch_stopiteration:
            # TODO maybe catch `ValueError: generator already executing` and try
            # to resend until it is paused? Could lead to lockups and would be a
            # send_self parameter. Same for ._throw.
            try:
                return generator.send(value)
            except StopIteration:
                return None
        else:
            generator.send(value)

    @property
    def throw(self):
        return partial(self._throw, self.generator)

    def _throw(self, generator, *args, **kwargs):
        if self.debug:
            print("throw:", generator, args, kwargs)
        if self.catch_stopiteration:
            try:
                return generator.throw(*args, **kwargs)
            except StopIteration:
                return None
        else:
            generator.throw(*args, **kwargs)

    @property
    def close(self):
        return self.generator.close


class WeakGeneratorWrapper(GeneratorWrapperBase):

    def __init__(self, generator, *args, **kwargs):
        self.weak_generator = weakref.ref(generator)
        super(WeakGeneratorWrapper, self).__init__(*args, **kwargs)

    @property
    def generator(self):
        return self.weak_generator()

    def with_strong_ref(self):
        return StrongGeneratorWrapper(self.generator, self.catch_stopiteration,
                                      self.debug)

    def with_weak_ref(self):
        return self

    __call__ = with_strong_ref


class StrongGeneratorWrapper(GeneratorWrapperBase):

    def __init__(self, generator, *args, **kwargs):
        self.generator = generator
        super(StrongGeneratorWrapper, self).__init__(*args, **kwargs)

    @property
    def weak_generator(self):
        return weakref.ref(self.generator)

    def with_strong_ref(self):
        return self

    def with_weak_ref(self):
        return WeakGeneratorWrapper(self.generator, self.catch_stopiteration,
                                    self.debug)

    __call__ = with_strong_ref  # Always return strong-referenced variant


def send_self(catch_stopiteration=True, finalize_callback=None, debug=False):
    """Decorator that sends a generator a wrapper of itself.

    When a generator decorated by this is called, it gets sent a wrapper of
    itself via the first 'yield' used. The wrapper is an instance of
    WeakGeneratorWrapper.

    Useful for creating generators that can leverage callback-based functions in
    a linear style, by passing the wrapper as callback in the first yield
    statement.

    The wrapper catches StopIteration exceptions by default. If you wish to have
    them propagated, set catch_stopiteration to `False`. Forwarded to the
    Wrapper.
    """
    # "catch_stopiteration" needs to be the name of the first parameter. For
    # clarity, we mirror that to first_param and override catch_stopiteration
    # later.
    first_param = catch_stopiteration
    catch_stopiteration = True

    # We either directly call this, or return it to be called by Python's
    # decorator mechanism.
    def _send_self(func):
        @wraps(func)
        def send_self_wrapper(*args, **kwargs):
            # optional but for clarity
            nonlocal catch_stopiteration, finalize_callback, debug

            # Create generator
            generator = func(*args, **kwargs)

            # Register finalize_callback to be called when the object is gc'ed
            weakref.ref(generator, finalize_callback)

            # The first yielded value will be used as return value of the
            # "initial call to the generator" (=> this wrapper).
            ret_value = next(generator)  # Start generator

            # Send wrapper to the generator
            gen_wrapper = WeakGeneratorWrapper(generator, catch_stopiteration,
                                               debug)
            generator.send(gen_wrapper)

            return ret_value

        return send_self_wrapper

    # If the argument is a callable, we've been used without being directly
    # passed an argument by the user, and thus should call _send_self directly.
    if callable(first_param):
        # No arguments, this is the decorator.
        return _send_self(first_param)
    else:
        # Someone has called @send_self(...) with parameters and thus we need to
        # return _send_self to be called indirectly.
        catch_stopiteration = first_param
        return _send_self


################################################################################
################################################################################
################################################################################


# Set to true for very detailed debug printing (forwarded to @send_self
# decorator).
DEBUG = True


# Following are a few funtions that are utilized to:
# 1. Print detailed debug information about the objects used and their
#    references.
# 2. Show case a few uses of generators as co-routines, such as throwing an
#    exception.

def monitor_refcounts(ref):
    oldweak, oldstrong = 0, 0
    print("start minitoring with", ref)
    while True:
        time.sleep(0.05)

        obj = ref()
        if not obj:
            break
        newweak, newstrong = weakref.getweakrefcount(ref), sys.getrefcount(obj)
        del obj

        msg = ("weak refcount: %d - strong refcount: %d"
               % (newweak, newstrong))
        sublime.status_message(msg)

        if (newweak, newstrong) != (oldweak, oldstrong):
            oldweak, oldstrong = newweak, newstrong
            print(msg)

    print("Object was garbage collected", ref)
    sublime.status_message("Object was garbage collected")


def defer(callback, call=True):

    def func():
        time.sleep(0.4)
        if call:
            callback()
        else:
            print("generator is not re-called")

    threading.Thread(target=func).start()


def test_throw(gw, i):

    def func():
        time.sleep(0.4)
        if i >= 3:
            ret = gw.throw(TypeError, "%d is greater than 2" % i)
            print("catched and returned:", ret)  # should be the above message
            gw.send()  # resume
        else:
            gw.send(i * 10)

    threading.Thread(target=func).start()


def sub_generator(this):
    print("waiting in sub_generator")
    yield sublime.set_timeout(this.send, 200)
    print("resumed in sub_generator")

    try:
        yield test_throw(this(), 300)
    except TypeError as e:
        print("We passed 300, but", e)
        yield "yeah, that was unreasonable"


class TestCommandCommand(sublime_plugin.WindowCommand):

    def wont_be_finished(self):
        this = yield
        if this.debug:
            threading.Thread(target=monitor_refcounts,
                             args=[this.weak_generator]).start()

        print("wont_be_finished")
        # this is where the initial caller will be resumed
        yield defer(this.send)
        print("middle~")
        yield defer(this.send, False)
        print("this should not be printed")

    @send_self(finalize_callback=lambda x: print("finalized"), debug=DEBUG)
    def run(self):
        this = yield
        if this.debug:
            threading.Thread(target=monitor_refcounts,
                             args=[this.weak_generator]).start()

        print("original weak-ref variant:", this)
        this = this()
        print("strong-ref variant:", this)
        this = this.with_weak_ref()
        print("new weak-ref variant:", this)

        yield defer(this.send)
        print("one")
        yield sublime.set_timeout(this.send, 200)
        print("one.one")

        for i in range(5):
            try:
                ret = yield test_throw(this(), i)
            except TypeError as e:
                print("oops!", e)
                yield "sorry"  # we are resumed by the test_throw thread
                break
            else:
                print("result", i, ret)
        print("two")

        # Utilize a sub-generator and pass the wrapper as argument so that it
        # can have data sent to itself (even exceptions).
        # Only for Python 3.3! (new syntax)
        yield from sub_generator(this)

        # Different method to invoke a sub-generator (and less effective)
        wont_be_finished = send_self(
            finalize_callback=this.send,
            debug=this.debug
        )(self.wont_be_finished)

        print("launching weird sub-generator")
        old_obj = yield wont_be_finished()
        print("weakref of other sub-generator:", old_obj)

        # text = yield self.window.show_input_panel("Enter stuff", '', this.send,
        #                                           None, None)
        # print(text)

        # Now, make reference strong and cause cyclic reference
        # DON'T TRY THIS AT HOME! MEMORY LEAK!
        # this = this()
        # yield


class Test2CommandCommand(sublime_plugin.WindowCommand):
    # This is a sub-generator
    def prompt(self, this, caption):
        return (yield self.window.show_input_panel(caption, '', this.send,
                                                   None, None))

    @send_self(finalize_callback=lambda x: print("finalized"))
    def run(self):
        this = yield  # This should be the first line

        text = yield self.window.show_input_panel("Enter something", '',
                                                  this.send, None, None)
        print("Entered the following text:", text)

        more_text = [(yield from self.prompt(this,
                                             "Please enter some more text " + i)
                      ) for i in range(4)]

        selection = yield self.window.show_quick_panel(more_text, this.send)
        if selection == -1:
            print("No selection made")
        else:
            print("Selected:", more_text[selection])
