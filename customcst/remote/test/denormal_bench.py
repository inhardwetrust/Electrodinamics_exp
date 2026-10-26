# denormal_bench.py: does this CPU slow down on denormal float32 (FDTD-like update)?
# python denormal_bench.py      (~1 min, needs numpy + numba)
import sys
import time

import numpy as np
from llvmlite import ir
from numba import get_num_threads, njit, prange, types
from numba.core import cgutils
from numba.extending import intrinsic


@intrinsic
def _mxcsr(typingctx, flags):
    """Return MXCSR; with flags != 0 also OR them in (0x8040 = FTZ | DAZ)."""
    sig = types.uint32(types.uint32)

    def codegen(context, builder, signature, args):
        i32 = ir.IntType(32)
        slot = cgutils.alloca_once(builder, i32)
        p8 = builder.bitcast(slot, ir.IntType(8).as_pointer())
        fnty = ir.FunctionType(ir.VoidType(), [ir.IntType(8).as_pointer()])
        st = cgutils.get_or_insert_function(builder.module, fnty, "llvm.x86.sse.stmxcsr")
        ld = cgutils.get_or_insert_function(builder.module, fnty, "llvm.x86.sse.ldmxcsr")
        builder.call(st, [p8])
        old = builder.load(slot)
        builder.store(builder.or_(old, args[0]), slot)
        builder.call(ld, [p8])
        return old
    return sig, codegen


@njit
def mxcsr_here(flags):
    return _mxcsr(np.uint32(flags))


@njit(parallel=True)
def mxcsr_all(flags, n):
    out = np.empty(n, np.uint32)
    for i in prange(n):
        _mxcsr(np.uint32(flags))
        out[i] = _mxcsr(np.uint32(0))
    return out


@njit(parallel=True, fastmath=False)
def update(e, h, ca, cb):
    nx, ny, nz = e.shape
    for i in prange(nx):
        for j in range(1, ny):
            for k in range(nz):
                e[i, j, k] = ca * e[i, j, k] + cb * (h[i, j, k] - h[i, j - 1, k])


def bench(label, value, steps=20):
    e = np.full(SHAPE, value, np.float32)
    h = np.full(SHAPE, value, np.float32)
    h[:, ::2, :] *= 2                       # a nonzero curl, same magnitude as the fields
    update(e, h, np.float32(0.99), np.float32(0.5))
    t = time.perf_counter()
    for _ in range(steps):
        update(e, h, np.float32(0.99), np.float32(0.5))
    dt = (time.perf_counter() - t) / steps
    print(f"{label:34s} {dt * 1e3:8.1f} ms/update   e[5,5,5] = {e[5, 5, 5]:.3g}")
    return dt


SHAPE = (400, 400, 150)
print(f"numba threads {get_num_threads()}, array {SHAPE}, float32")
normal = bench("normal values (1.0)", 1.0)
slow = bench("denormal values (1e-39)", 1e-39)
before = mxcsr_all(0, 10000)
print(f"MXCSR in workers before: {sorted(set(hex(v) for v in before))}")
mxcsr_here(0x8040)
after = mxcsr_all(0x8040, 10000)
print(f"MXCSR in workers after:  {sorted(set(hex(v) for v in after))}")
fixed = bench("denormal values, FTZ+DAZ on", 1e-39)
print(f"\ndenormal slowdown {slow / normal:.1f}x; with FTZ/DAZ {fixed / normal:.1f}x")
sys.exit(0)
