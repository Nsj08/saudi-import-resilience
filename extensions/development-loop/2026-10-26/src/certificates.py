"""Exact deterministic certificates for G/O/P uncertainty coupling."""
from fractions import Fraction as F
from itertools import product


def f(x):
    return x if isinstance(x, F) else F(str(x))


def clipped(x):
    return max(F(0), x)


def bounds(gamma, delta):
    return max(F(0), gamma-delta), min(F(1), gamma+delta)


def outcome(c, a, rates):
    if len(a)!=len(rates): raise ValueError('coefficient/rate length mismatch')
    return clipped(c-sum((x*y for x,y in zip(a,rates)), F(0)))


def coeffs(model, ap, aq, origins):
    if model == 'G': return [sum(ap,F(0))-sum(aq,F(0))]
    if model == 'O': return [x-y for x,y in zip(ap,aq)]
    if model == 'P': return [sum(ap,F(0)), -sum(aq,F(0))]
    raise ValueError(model)


def linear_min(intercept, bs, lo, hi):
    return intercept-sum((b*hi if b>0 else b*lo for b in bs),F(0))


def loss_values(model, cp, cq, ap, aq, gamma, delta):
    lo,hi=bounds(gamma,delta)
    b=coeffs(model,ap,aq,None)
    return cp-sum(ap,F(0))*hi, cp-cq-sum((x*hi if x>0 else x*lo for x in b),F(0))


def first_nonpositive(fn, gamma):
    """Exact first root of continuous piecewise-linear nonincreasing fn."""
    pts=sorted(set([F(0),gamma,1-gamma,F(1)]))
    vals=[fn(x) for x in pts]
    if vals[0] <= 0: return F(0)
    for i in range(len(pts)-1):
        x0,x1=pts[i:i+2]; y0,y1=vals[i:i+2]
        if y1 <= 0:
            if y1 == y0: return x1
            return x0+(x1-x0)*y0/(y0-y1)
    return None


def pair_radius(model, cp,cq,ap,aq,gamma):
    b=coeffs(model,ap,aq,None)
    r1=first_nonpositive(lambda d: loss_values(model,cp,cq,ap,aq,gamma,d)[0],gamma)
    r2=first_nonpositive(lambda d: loss_values(model,cp,cq,ap,aq,gamma,d)[1],gamma)
    finite=[x for x in (r1,r2) if x is not None]
    return min(finite) if finite else None


def independent_interval_radius(cp,cq,ap,aq,gamma):
    """Independent marginal-interval endpoint baseline, solved from endpoints."""
    ap=sum(ap,F(0)); aq=sum(aq,F(0))
    pts={F(0),F(1),gamma,1-gamma}
    # Add clipping breakpoints for each marginal endpoint.
    if ap:
        x=cp/ap-gamma
        if 0<x<1-gamma: pts.add(x)
    if aq:
        x=gamma-cq/aq
        if 0<x<gamma: pts.add(x)
    pts=sorted(pts)
    def gap(delta):
        lo,hi=bounds(gamma,delta)
        return clipped(cp-ap*hi)-clipped(cq-aq*lo)
    ys=[gap(x) for x in pts]
    if ys[0]<=0:return F(0)
    for i in range(len(pts)-1):
        x0,x1=pts[i:i+2];y0,y1=ys[i:i+2]
        if y1<=0:
            return x0 if y0==y1 else x0+(x1-x0)*y0/(y0-y1)
    return None

def h1_qualifies(g_radius, p_radius, strict_reversal_found):
    """H1 predicate; None means full-domain only for G, never for P."""
    threshold=F(1,20)
    return (g_radius is None or g_radius>threshold) and p_radius is not None and p_radius<=threshold and bool(strict_reversal_found)
