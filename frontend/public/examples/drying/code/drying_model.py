
"""Cylindrical (1D radial) coupled heat & moisture transfer solver for herb drying.

Governing equations (axisymmetric cylinder, t>0, 0<r<R(t)):
  heat :  rho(C) cp(C) dT/dt = (1/r) d/dr ( r k(C) dT/dr )
  moist:  dC/dt = (1/r) d/dr ( r D(C,T) dC/dr )
  r=0 : symmetry ;  r=R(t):  -k dT/dr = h (T - T_air(t)),  -D dC/dr = hm (C - C_air(t))
Shrinkage is prescribed by R(t) (附件2).  Using the Lagrangian coordinate
xi = r/R(t) the conservative form for a uniformly shrinking cylinder is
  rho cp R^2 xi dxi dT/dtau = [ xi_f k dT/dxi ]_{faces}
i.e. storage scales with R^2 and boundary fluxes with R (no advection term
appears for r-linear shrinkage velocity).  Discretisation: cell-centred finite
volume in xi, backward Euler in time, Picard iteration for the nonlinear
properties.
"""
import numpy as np
from scipy.linalg import solve_banded
import pandas as pd

R0 = 0.02  # m


# ---------------------------------------------------------------- properties
class PropsQ1:
    """附录2 (problem 1)."""
    h, hm = 25.0, 8.0e-7
    def rho(self, C): return np.full_like(C, 820.0)
    def cp(self, C):  return np.full_like(C, 2600.0)
    def k(self, C):   return np.full_like(C, 0.36)
    def D(self, C, T): return 7.0e-9 * np.exp(-0.89 / np.maximum(C, 1e-8))


class PropsQ23:
    """附录3 (problems 2, 3)."""
    h, hm = 25.0, 8.0e-7
    def rho(self, C): return 650.0 + 128.0 * C
    def cp(self, C):  return 1450.0 + 2736.0 * C / (C + 1.0)
    def k(self, C):   return 0.21 + 0.38 * C / (C + 1.0)
    def D(self, C, T):
        return (2.4e-3 * np.exp(-0.45 / np.maximum(C, 1e-8))
                * np.exp(-3850.0 / (T + 273.15)))


class PropsQ4:
    """附录4 (problem 4, shrinkage from 附件2)."""
    h, hm = 25.0, 8.0e-7
    def rho(self, C): return 760.0 + 90.0 * C
    def cp(self, C):  return 1850.0 + 2150.0 * C / (C + 1.0)
    def k(self, C):   return 0.12 + 0.20 * C / (C + 1.0)
    def D(self, C, T):
        return (4.2e-4 * np.exp(-0.30 / np.maximum(C, 1e-8))
                * np.exp(-3850.0 / (T + 273.15)))


# ---------------------------------------------------------------- ambient air
def load_ambient(path='attachment/附件1.xlsx'):
    df = pd.read_excel(path)
    return (df['时间'].values.astype(float), df['温度'].values.astype(float),
            df['水分浓度'].values.astype(float))


class Ambient:
    """Measured air state (附件1) up to t_switch, then constant drying stage."""
    def __init__(self, t, Ta, Ca, t_switch=14400.0, T_const=50.0, C_const=0.05):
        self.t, self.Ta, self.Ca = t, Ta, Ca
        self.t_switch, self.T_const, self.C_const = t_switch, T_const, C_const

    def __call__(self, tt):
        if tt <= self.t_switch:
            return float(np.interp(tt, self.t, self.Ta)), float(np.interp(tt, self.t, self.Ca))
        return self.T_const, self.C_const


def load_radius(path='attachment/附件2.xlsx'):
    df = pd.read_excel(path)
    return df['时间'].values.astype(float), df['半径'].values.astype(float) / 100.0  # m


class Radius:
    def __init__(self, t, R):
        self.t, self.R = np.asarray(t, float), np.asarray(R, float)
    def __call__(self, tt):
        if tt >= self.t[-1]:
            return float(self.R[-1])
        return float(np.interp(tt, self.t, self.R))


class ConstRadius:
    def __init__(self, R=R0): self.Rv = R
    def __call__(self, tt): return self.Rv


# ---------------------------------------------------------------- solver
def _tridiag(lower, diag, upper, rhs):
    n = len(diag)
    ab = np.zeros((3, n))
    ab[0, 1:] = upper[:-1]
    ab[1, :] = diag
    ab[2, :-1] = lower[1:]
    return solve_banded((1, 1), ab, rhs)


def solve_cylinder(props, ambient, radius, t_end, dt, N=120, T0=28.0, C0=2.55,
                   r_samp=None, n_picard=3, save_every=1, omega=0.6):
    """Backward-Euler FV integration.

    Returns dict(times, Tr, Cr, Tsurf, Csurf, flux_h, flux_m, Tair_t, Cair_t)
    where Tr/Cr are sampled at r_samp (m, absolute radius) and the flux series
    are the heat (W/m2) and moisture (kg/(m2 s)) fluxes leaving the surface.
    """
    if r_samp is None:
        r_samp = np.arange(0, 0.0200001, 0.001)
    xi_f = np.arange(N + 1) / N
    xi_c = 0.5 * (xi_f[1:] + xi_f[:-1])
    dxi = 1.0 / N
    Avol = xi_f[1:] ** 2 - xi_f[:-1] ** 2

    T = np.full(N, T0); C = np.full(N, C0)
    nt = int(round(t_end / dt))
    nsave = nt // save_every + 1
    out = dict(times=np.zeros(nsave), Tr=np.zeros((nsave, len(r_samp))),
               Cr=np.zeros((nsave, len(r_samp))), Tsurf=np.zeros(nsave),
               Csurf=np.zeros(nsave), flux_h=np.zeros(nsave), flux_m=np.zeros(nsave),
               Tair=np.zeros(nsave), Cair=np.zeros(nsave), R=np.zeros(nsave),
               Mwater=np.zeros(nsave), Fcum=np.zeros(nsave))
    Fcum = 0.0
    ksave = 0

    def record(tt):
        nonlocal ksave
        R = radius(tt)
        Tair, Cair = ambient(tt)
        dr_ = R * dxi
        # surface values extrapolated from the surface energy/mass balance
        aT = props.h * (dr_ / 2) / props.k(C[-1])
        aC = props.hm * (dr_ / 2) / props.D(C[-1], T[-1])
        Ts = (T[-1] + aT * Tair) / (1.0 + aT)
        Cs = (C[-1] + aC * Cair) / (1.0 + aC)
        r_ext = np.append(R * xi_c, R)
        out['times'][ksave] = tt
        out['R'][ksave] = R
        out['Tair'][ksave] = Tair; out['Cair'][ksave] = Cair
        out['Tr'][ksave] = np.interp(r_samp, r_ext, np.append(T, Ts))
        out['Cr'][ksave] = np.interp(r_samp, r_ext, np.append(C, Cs))
        out['Tsurf'][ksave] = Ts; out['Csurf'][ksave] = Cs
        out['Mwater'][ksave] = 2 * np.pi * R ** 2 * np.sum(C * xi_c * dxi)
        out['Fcum'][ksave] = Fcum
        ksave += 1
    record(0.0)

    for n in range(1, nt + 1):
        t_new = n * dt
        Rnew = radius(t_new)
        Tair, Cair = ambient(t_new)
        dr = Rnew * dxi
        Tp, Cp = T.copy(), C.copy()
        for it in range(n_picard):
            rho = props.rho(Cp); cp = props.cp(Cp); kk = props.k(Cp)
            kap = rho * cp
            Dc = props.D(Cp, Tp)
            kf = 2 * kk[:-1] * kk[1:] / (kk[:-1] + kk[1:])
            Df = 2 * Dc[:-1] * Dc[1:] / (Dc[:-1] + Dc[1:])
            Gs = 1.0 / (1.0 / props.h + 0.5 * dr / kk[-1])
            Hms = 1.0 / (1.0 / props.hm + 0.5 * dr / Dc[-1])

            cap = kap * Rnew ** 2 * xi_c * dxi / dt
            cf = xi_f[1:-1] * kf / dxi
            di = cap.copy(); lo = np.zeros(N); up = np.zeros(N); rhs = cap * T
            di[:-1] += cf; up[:-1] -= cf
            di[1:] += cf; lo[1:] -= cf
            cb = Gs * Rnew
            di[-1] += cb; rhs[-1] += cb * Tair
            Tnew = _tridiag(lo, di, up, rhs)

            capc = Rnew ** 2 * xi_c * dxi / dt
            cfc = xi_f[1:-1] * Df / dxi
            di2 = capc.copy(); lo2 = np.zeros(N); up2 = np.zeros(N); rhs2 = capc * C
            di2[:-1] += cfc; up2[:-1] -= cfc
            di2[1:] += cfc; lo2[1:] -= cfc
            cbc = Hms * Rnew
            di2[-1] += cbc; rhs2[-1] += cbc * Cair
            Cnew = _tridiag(lo2, di2, up2, rhs2)
            if it < n_picard - 1:
                Tp = (1 - omega) * Tp + omega * Tnew
                Cp = (1 - omega) * Cp + omega * Cnew
        T, C = Tnew, Cnew
        Cs_now = (C[-1] + (props.hm * (Rnew * dxi / 2) / props.D(C[-1], T[-1])) * Cair) / \
                 (1.0 + props.hm * (Rnew * dxi / 2) / props.D(C[-1], T[-1]))
        Fcum += 2 * np.pi * Rnew * props.hm * (Cs_now - Cair) * dt
        if n % save_every == 0:
            record(t_new)
            out['flux_h'][ksave - 1] = props.h * (T[-1] - Tair)
            out['flux_m'][ksave - 1] = props.hm * (C[-1] - Cair)
    for key in ('times', 'Tr', 'Cr', 'Tsurf', 'Csurf', 'flux_h', 'flux_m', 'Tair', 'Cair', 'R',
                'Mwater', 'Fcum'):
        out[key] = out[key][:ksave]
    return out
