import abc

import numpy as np
from numpy import sin, cos, arctan, dot
from numpy.polynomial import Polynomial
from scipy.interpolate import interp1d
from scipy.interpolate import UnivariateSpline
from scipy.interpolate import CubicSpline

import matplotlib.pyplot as plt

from util import trapz

from IPython import embed


class Parafoil:
    def __init__(self, geometry, airfoil, wing_density=0.2):
        self.geometry = geometry
        self.airfoil = airfoil
        self.wing_density = wing_density  # FIXME: no idea in general

    @property
    def density_factor(self):
        # FIXME: I don't understand this. Ref: PFD 48 (56)
        return self.geometry.MAC * self.airfoil.t*self.airfoil.chord/3

    def fE(self, y, xa=None, N=150):
        """Airfoil upper camber line on the 3D wing

        Parameters
        ----------
        y : float
            Position on the span, where `-b/2 < y < b/2`
        xa : float or array of float, optional
            Positions on the chord line, where all `0 < xa < chord`
        N : integer, optional
            If xa is `None`, sample `N` points along the chord
        """

        if xa is None:
            xa = np.linspace(0, 1, N)

        fc = self.geometry.fc(y)  # Chord length at `y` on the span
        upper = fc*self.airfoil.geometry.fE(xa)  # Scaled airfoil
        xs, zs = upper[:, 0], upper[:, 1]

        theta = self.geometry.ftheta(y)
        Gamma = self.geometry.Gamma(y)

        x = self.geometry.fx(y) + (fc/4 - xs)*cos(theta) - zs*sin(theta)
        _y = y + ((fc/4 - xs)*sin(theta) + zs*cos(theta))*sin(Gamma)
        z = self.geometry.fz(y) - \
            ((fc/4 - xs)*sin(theta) + zs*cos(theta))*cos(Gamma)

        return np.c_[x, _y, z]

    def fI(self, y, xa=None, N=150):
        """Airfoil lower camber line on the 3D wing

        Parameters
        ----------
        y : float
            Position on the span, where `-b/2 < y < b/2`
        xa : float or array of float, optional
            Positions on the chord line, where all `0 < xa < chord`
        N : integer, optional
            If xa is `None`, sample `N` points along the chord
        """

        if xa is None:
            xa = np.linspace(0, 1, N)

        fc = self.geometry.fc(y)  # Chord length at `y` on the span
        upper = fc*self.airfoil.geometry.fI(xa)  # Scaled airfoil
        xs, zs = upper[:, 0], upper[:, 1]

        theta = self.geometry.ftheta(y)
        Gamma = self.geometry.Gamma(y)

        x = self.geometry.fx(y) + (fc/4 - xs)*cos(theta) + zs*sin(theta)
        _y = y + ((fc/4 - xs)*sin(theta) + zs*cos(theta))*sin(Gamma)
        z = self.geometry.fz(y) - \
            ((fc/4 - xs)*sin(theta) + zs*cos(theta))*cos(Gamma)

        return np.c_[x, _y, z]


class CoefficientsEstimator(abc.ABC):
    @abc.abstractmethod
    def Cl(self, y, alpha, delta_Bl, delta_Br):
        """The lift coefficient for the parafoil section"""

    @abc.abstractmethod
    def Cd(self, y, alpha, delta_Bl, delta_Br):
        """The drag coefficient for the parafoil section"""

    @abc.abstractmethod
    def Cm(self, y, alpha, delta_Bl, delta_Br):
        """The pitching moment coefficient for the parafoil section"""

    def _pointwise_global_coefficients(self, alpha, delta_B):
        """
        Compute point estimates of the global CL, CD, and Cm

        This procedure is from Paraglider Flight Dynamics, Sec:4.3.3

        Parameters
        ----------
        alpha : float [radians]
            The angle of attack for the current point estimate
        delta_B : float [percentage]
            The amount of symmetric brakes, where `0 <= delta_b <= 1`

        Returns
        -------
        CL : float
            The global lift coefficient
        CD : float
            The global drag coefficient
        CM_c4 : float
            The global pitching moment coefficient about quarter chord of the
            central chord.
        """

        # Build a set of integration points
        N = 501
        dy = self.parafoil.geometry.b/(N - 1)  # Include the endpoints
        y = np.linspace(-self.parafoil.geometry.b/2,
                        self.parafoil.geometry.b/2, N)

        # Compute local relative winds to match `alpha`
        uL, wL = np.cos(alpha), np.sin(alpha)

        # Compute the local forces
        dF, dM = self.section_forces(y, uL, 0, wL, delta_B, delta_B)
        dFx, dFz, dMy = dF[:, 0], dF[:, 2], dM[:, 1]  # Convenience views

        # Convert the body-oriented forces into relative wind coordinates
        # PFD Eq:4.29-4.30, p77
        Li_prime = dFx*sin(alpha) - dFz*cos(alpha)
        Di_prime = -dFx*cos(alpha) - dFz*sin(alpha)
        L = trapz(Li_prime, dy)
        D = trapz(Di_prime, dy)
        My = trapz(dMy, dy)

        # Relocating the dFx and dFz to the central quarter chord introduces
        # new pitching moments. These must be subtracted from the overall
        # pitching moment for the global force+moment pair to be equivalent.
        c4 = self.parafoil.geometry.fx(0)
        x = self.parafoil.geometry.fx(y)
        z = self.parafoil.geometry.fz(y)
        mFx = trapz(dFx*(0-z), dy)  # The moment from relocating dFx to c4
        mFz = -trapz(dFz*(c4-x), dy)  # The moment from relocating dFz to c4
        My_c4 = My - mFx - mFz  # Apply counteracting moments for equivalence

        # Compute the global coefficients
        # Note: in this context, rho=1 and V_infinity=1 and have been dropped
        S = self.parafoil.geometry.S
        MAC = self.parafoil.geometry.MAC
        CL = L / (1/2 * S)
        CD = D / (1/2 * S)
        CM_c4 = My_c4 / (1/2 * S * MAC)

        return CL, CD, CM_c4

    # FIXME: this doesn't seem to belong in a "CoefficientsEstimator"!
    def section_forces(self, y, uL, vL, wL, delta_Bl=0, delta_Br=0, rho=1):
        """
        Compute section forces and moments acting on the wing.

        Parameters
        ----------
        y : float or array of float
            The section position on the wing span, where -b/2 < y < b/2
        uL : float or array of float
            Section-local relative wind aligned to the Parafoil x-axis
        vL : float or array of float
            Section-local relative wind aligned to the Parafoil y-axis
        wL : float or array of float
            Section-local relative wind aligned to the Parafoil z-axis
        delta_Bl : float [percentage]
            The amount of left brake
        delta_Br : float [percentage]
            The amount of right brake
        rho : float
            Air density

        Returns
        -------
        dF : ndarray, shape (M, 3)
            The force differentials parallel to the X, Y, and Z axes
        dM : ndarray, shape (M, 3)
            The moment differentials about the X, Y, and Z axes
        """

        # FIXME: verify docstring. These are forces per unit span, not the

        Gamma = self.parafoil.geometry.Gamma(y)  # PFD eq 4.13, p74
        theta = self.parafoil.geometry.ftheta(y)

        # Compute the section (local) relative wind
        u = uL  # PFD Eq:4.14, p74
        w = wL*cos(Gamma) - vL*sin(Gamma)  # PFD Eq:4.15, p74
        alpha = arctan(w/u) + theta  # PFD Eq:4.17, p74

        Cl = self.Cl(y, alpha, delta_Bl, delta_Br)
        Cd = self.Cd(y, alpha, delta_Bl, delta_Br)
        Cm = self.Cm(y, alpha, delta_Bl, delta_Br)

        # PFD Eq:4.65-4.67 (with an implicit `dy` term)
        c = self.parafoil.geometry.fc(y)
        K1 = (rho/2)*(u**2 + w**2)
        K2 = 1/cos(Gamma)
        dL = K1*Cl*c*K2
        dD = K1*Cd*c*K2
        dm0 = K1*Cm*(c**2)*K2

        # Translate the section forces and moments into Parafoil body axes
        #  * PFD Eqs:4.23-4.28, pp76-77
        dF_perp_x = dL*cos(alpha - theta) + dD*sin(alpha - theta)
        dF_par_x = dL*sin(alpha - theta) - dD*cos(alpha - theta)
        dF_par_y = dF_perp_x * sin(Gamma)
        dF_par_z = -dF_perp_x * cos(Gamma)
        dM_par_x = np.zeros_like(y)
        dM_par_y = dm0*cos(Gamma)
        dM_par_z = -dm0*sin(Gamma)  # FIXME: verify

        dF = np.vstack([dF_par_x, dF_par_y, dF_par_z]).T
        dM = np.vstack([dM_par_x, dM_par_y, dM_par_z]).T

        return dF, dM


class Coefs2D(CoefficientsEstimator):
    """
    Returns the 2D airfoil coefficients with no adjustments.

    FIXME: Only works for parafoils with constant airfoils
    """

    def __init__(self, parafoil, brake_geo):
        self.parafoil = parafoil
        self.brake_geo = brake_geo

        # Compute 25 Polynomial approxmations over the operating brake range
        self.delta_Bs = np.linspace(0, 1, 25)

        # Initialize the global aerodynamic coefficients
        CLs, CDs, CM_c4s = self._compute_global_coefficients()
        self.CLs = CLs
        self.CDs = CDs
        self.CM_c4s = CM_c4s

    def Cl(self, y, alpha, delta_Bl, delta_Br):
        if np.isscalar(alpha):
            alpha = np.ones_like(y) * alpha
        delta = self.brake_geo(y, delta_Bl, delta_Br)
        return self.parafoil.airfoil.coefficients.Cl(alpha, delta)

    def Cd(self, y, alpha, delta_Bl, delta_Br):
        if np.isscalar(alpha):
            alpha = np.ones_like(y) * alpha
        delta = self.brake_geo(y, delta_Bl, delta_Br)
        return self.parafoil.airfoil.coefficients.Cd(alpha, delta)

    def Cm(self, y, alpha, delta_Bl, delta_Br):
        if np.isscalar(alpha):
            alpha = np.ones_like(y) * alpha
        delta = self.brake_geo(y, delta_Bl, delta_Br)
        return self.parafoil.airfoil.coefficients.Cm0(alpha, delta)

    # FIXME: doesn't do any interpolation between deltas
    def CL(self, alpha, delta_B):
        di = np.argmin(np.abs(self.delta_Bs - delta_B))
        return self.CLs[di](alpha)

    def CD(self, alpha, delta_B):
        di = np.argmin(np.abs(self.delta_Bs - delta_B))
        return self.CDs[di](alpha)

    def CM_c4(self, alpha, delta_B):
        di = np.argmin(np.abs(self.delta_Bs - delta_B))
        return self.CM_c4s[di](alpha)

    def _compute_global_coefficients(self):
        alphas = np.deg2rad(np.linspace(-1.99, 24, 100))
        _CLs, _CDs, _CM_c4s = [], [], []

        for n_d, delta_B in enumerate(self.delta_Bs):
            CLs = np.empty_like(alphas)
            CDs = np.empty_like(alphas)
            CM_c4s = np.empty_like(alphas)

            for n_a, alpha in enumerate(alphas):
                CL, CD, CM_c4 = self._pointwise_global_coefficients(
                        alpha, delta_B)
                CLs[n_a], CDs[n_a], CM_c4s[n_a] = CL, CD, CM_c4

            CL = Polynomial.fit(alphas, CLs, 5)
            CD = Polynomial.fit(alphas, CDs, 5)
            CM_c4 = Polynomial.fit(alphas, CM_c4s, 2)

            _CLs.append(CL)
            _CDs.append(CD)
            _CM_c4s.append(CM_c4)

        return _CLs, _CDs, _CM_c4s


class CoefsPFD(CoefficientsEstimator):
    """
    The coefficient estimation method from Paraglider Flight Dynamics

    This converts the wing into a uniform straight wing, so it eliminates the
    dependency on the span position.
    """

    def __init__(self, parafoil, brake_geo):
        self.parafoil = parafoil
        self.brake_geo = brake_geo

        # Initialize the global (3D) aerodynamic coefficients
        CL, CD, CM = self._compute_global_coefficients()
        self.CL = CL
        self.CD = CD
        self.CM = CM

        # Initialize the section (2D) aerodynamic coefficients
        # FIXME

    def Cl(self, y, alpha, delta_Br, delta_Bl):
        # TODO: test, verify, and adapt for `delta`
        # TODO: document
        i0 = self.CL.roots()[0]  # Adjusted global zero-lift angle
        return self.a_bar * (alpha - i0)

    def Cd(self, y, alpha, delta_Br, delta_Bl):
        # TODO: test, verify, and adapt for `delta`
        # TODO: document
        i0 = self.CL.roots()[0]  # Adjusted global zero-lift angle
        D0 = self.CD(i0)
        return self.D2_bar*(self.a_bar * (alpha - i0))**2 + D0

    def Cm(self, y, alpha, delta_Br, delta_Bl):
        # TODO: test, verify, and adapt for `delta`
        # TODO: document
        return self.Cm_bar

    def _compute_global_coefficients(self):
        """
        Fit polynomials to the adjusted global aerodynamic coefficients.

        This procedure is from Paraglider Flight Dynamics, Sec:4.3.4

        FIXME: the alpha range should depend on the airfoil.
        FIXME: currently assumes linear CL, with no expectation of stalls!!
        FIXME: for non-constant-linear airfoils, do these fittings hold?
        FIXME: seems convoluted for what it accomplishes
        """
        alphas = np.deg2rad(np.linspace(-1.99, 24, 1000))
        CLs = np.empty_like(alphas)
        CDs = np.empty_like(alphas)
        Cms = np.empty_like(alphas)

        # First, compute the unadjusted global coefficients
        coefs2d = Coefs2D(self.parafoil, self.brake_geo)
        for n, alpha in enumerate(alphas):
            CL, CD, Cm = coefs2d._pointwise_global_coefficients(alpha, delta_B=0)
            CLs[n], CDs[n], Cms[n] = CL, CD, Cm

        # Second, adjust the global coefficients to account for the 3D wing
        # FIXME: Verify!

        # FIXME: already losing data with a linear fit!!!
        # CL_prime = Polynomial.fit(alphas, CLs, 1)
        mask = (alphas > np.deg2rad(-3)) & (alphas < np.deg2rad(9))
        CL_prime = Polynomial.fit(alphas[mask], CLs[mask], 1)

        i0 = CL_prime.roots()[0]  # Unadjusted global zero-lift angle
        # i0 = alphas[np.argmin(np.abs(CLs))]

        a_prime = CL_prime.deriv()(0.05)  # Preliminary lift-curve slope
        a = a_prime/(1 + a_prime/(np.pi * self.parafoil.geometry.AR))  # Adjusted slope

        # FIXME: should not rely on the AirfoilCoefficients providing D0
        # D0 = self.airfoil.coefficients.D0  # Profile drag only
        D0 = self.parafoil.airfoil.coefficients.Cd(alphas)

        D2_prime = CDs/CLs**2 - D0
        D2 = D2_prime + 1/(np.pi * self.parafoil.geometry.AR)  # Adjusted induced drag

        # FIXME: this derivation and fitting for D2 seems strange to me. He
        # assumes a fixed D0 for the airfoil; the CDs are basically D0, but
        # with a small variation due to the 3D wing shape. Thus, aren't `CDs`
        # essentially the alpha-dependent profile drag? Then why does he
        # subtract D0 to calculate D2_prime, then add back D0 to calculate CD,
        # instead of subtracting and adding CDs, the "true" profile drag?

        # FIXME: compare these results against the wings on PFD pg 97

        CL = Polynomial.fit(alphas, a*(alphas - i0), 1)
        CD = Polynomial.fit(alphas, D2 * (CL(alphas)**2) + D0, 2)
        Cm = Polynomial.fit(alphas, Cms, 2)

        return CL, CD, Cm

    def _pointwise_local_coefficients(self, alpha, delta_B):
        """
        This is my replacement for PFD Sec:4.3.5
        """
        N = 501
        y = np.linspace(-self.parafoil.geometry.b/2,
                        self.parafoil.geometry.b/2, 501)
        dy = self.parafoil.geometry.b/(N - 1)

        Gamma = self.parafoil.geometry.Gamma(y)
        theta = self.parafoil.geometry.ftheta(y)
        c = self.parafoil.geometry.fc(y)
        S = self.parafoil.geometry.S

        uL, wL = np.cos(alpha), np.sin(alpha)
        ui = uL
        wi = wL*cos(Gamma)
        alpha_i = np.arctan(wi/ui) + theta

        i0 = self.CL.roots()[0]
        D0 = self.CD(i0)

        numerator = self.CL(alpha)*S
        denominator = trapz((alpha_i - i0)*c/cos(Gamma), dy)
        a_bar = numerator/denominator

        numerator = self.CD(alpha)*S - trapz(D0*c/cos(Gamma), dy)
        Cl = a_bar*(alpha_i - i0)
        denominator = trapz(Cl**2 * c / cos(Gamma), dy)
        D2_bar = numerator/denominator

        self.a_bar = a_bar
        self.D2_bar = D2_bar
        self.Cm_bar = self.parafoil.airfoil.coefficients.Cm0(alpha, 0)
        return a_bar, D2_bar

    def _pointwise_local_coefficients_PFD(self, alpha_eq):
        """
        This procedure is from PFD Sec:4.3.5
        """
        N = 501
        dy = self.parafoil.geometry.b/(N - 1)  # Include the endpoints
        y = np.linspace(-self.parafoil.geometry.b/2,
                        self.parafoil.geometry.b/2, 501)

        Gamma = self.parafoil.geometry.Gamma(y)
        theta = self.parafoil.geometry.ftheta(y)

        alpha_i = alpha_eq*cos(Gamma) + theta  # PFD Eq:4.46, p82

        CL_eq = self.CL(alpha_eq)
        CD_eq = self.CD(alpha_eq)
        Cm0_eq = self.Cm(alpha_eq)

        # Recompute some stuff from `global_coefficients`
        a = self.CL.deriv()(0)  # FIXME: is this SUPPOSED to be a_global?
        i0 = self.CL.roots()[0]  # FIXME: test
        D0 = self.CD(i0)  # Global D0
        D2 = (self.CD(alpha_eq) - D0) / CL_eq**2  # PFD eq 4.39

        tmp_i_local = alpha_i - self.parafoil.airfoil.coefficients.i0
        tmp_i_global = alpha_i - i0
        NZL = (1/10**5)**(1 - tmp_i_local/np.abs(tmp_i_local))

        fc = self.parafoil.geometry.fc(y)
        # PFD Equations 4.50-4.52 p74 (p82)
        # Note: the `dy` is left off at this point, and must be added later
        KX1 = NZL*tmp_i_global*sin(alpha_i - theta)/cos(Gamma)*fc
        KX2 = -(tmp_i_global**2)*cos(alpha_i - theta)/cos(Gamma)*fc
        KX3 = -D0*fc*cos(alpha_i - theta)/cos(Gamma)*fc

        # PFD Equations 4.53-4.55 p75 (p83)
        KZ1 = NZL*tmp_i_global*cos(alpha_i - theta)*fc
        KZ2 = (tmp_i_global**2)*sin(alpha_i - theta)*fc
        KZ3 = D0*fc*sin(alpha_i - theta)*fc

        # PFD equations 4.58-4.59, p75 (p83)
        KL1 = KZ1*cos(alpha_eq) + KX1*sin(alpha_eq)
        KL2 = KZ2*cos(alpha_eq) + KX2*sin(alpha_eq)
        KL3 = KZ3*cos(alpha_eq) + KX3*sin(alpha_eq)
        KD1 = KZ1*sin(alpha_eq) - KX1*cos(alpha_eq)
        KD2 = KZ2*sin(alpha_eq) - KX2*cos(alpha_eq)
        KD3 = KZ3*sin(alpha_eq) - KX3*cos(alpha_eq)

        # PFD eq 4.61, p76 (p84)
        SL1 = trapz(KL1, dy)
        SL2 = trapz(KL2, dy)
        SL3 = trapz(KL3, dy)
        SD1 = trapz(KD1, dy)
        SD2 = trapz(KD2, dy)
        SD3 = trapz(KD3, dy)

        S = self.parafoil.geometry.S

        # Version 1: From PFD; susceptible to divison by zero (via SL2)
        #  * Specifically: when torsion=0 and alpha=0 (and thus sin(alpha) = 0)
        # a_bar = (S*(SD2/SL2*a*(alpha_eq - i0) - (D2*CL_eq**2 + D0)) -
        #          (SD2/SL2*SL3-SD3)) / (SD2/SL2*SL1 - SD1)
        # D2_bar = (S*a*(alpha_eq - i0) - a_bar*SL1 - SL3)/(a_bar**2*SL2)

        # This version is less susceptible for division-by-zero?
        a_bar = (S*CL_eq - ((S*CD_eq - SD3)/SD2)*SL2 - SL3)/(SL1 - (SD1/SD2)*SL2)
        D2_bar = (S*CD_eq - a_bar*SD1 - SD3)/(a_bar**2 * SD2)

        Cm0_bar = Cm0_eq  # FIXME: correct? ref: "median", PFD p79

        # Update the section coefficients for calculating local forces
        self.a_bar = a_bar
        self.D2_bar = D2_bar
        self.Cm0_bar = Cm0_bar
        return a_bar, D2_bar, Cm0_bar


class CoefsMine(CoefficientsEstimator):
    # FIXME: docstring
    #  * A WIP mutating version of CoefsPFD

    def __init__(self, parafoil, brake_geo):
        self.parafoil = parafoil
        self.brake_geo = brake_geo  # FIXME: shouldn't need to keep this around

        # Initialize the global (3D) aerodynamic coefficients
        CL, CD, Cm = self._compute_global_coefficients()
        self.CL = CL
        self.CD = CD
        self.Cm = Cm

        # Initialize the section (2D) aerodynamic coefficients
        # FIXME

    def Cl(self, y, alpha, delta_Br, delta_Bl):
        raise NotImplementedError('Section coefficients not yet supported')

        if np.isscalar(alpha):
            alpha = np.ones_like(y) * alpha
        return self._Cl(y, alpha, delta_Br, delta_Bl)

    def Cd(self, y, alpha, delta_Br, delta_Bl):
        raise NotImplementedError('Section coefficients not yet supported')

        if np.isscalar(alpha):
            alpha = np.ones_like(y) * alpha
        return self._Cd(y, alpha, delta_Br, delta_Bl)

    def Cm(self, y, alpha, delta_Br, delta_Bl):
        raise NotImplementedError('Section coefficients not yet supported')

        if np.isscalar(alpha):
            alpha = np.ones_like(y) * alpha
        return self._Cm(y, alpha, delta_Br, delta_Bl)

    def _compute_global_coefficients(self):
        """
        Fit polynomials to the adjusted global aerodynamic coefficients.

        This procedure is from Paraglider Flight Dynamics, Sec:4.3.4

        FIXME: the alpha range should depend on the airfoil.
        FIXME: currently assumes linear CL, with no expectation of stalls!!
        FIXME: for non-constant-linear airfoils, do these fittings hold?
        FIXME: seems convoluted for what it accomplishes
        """
        alphas = np.deg2rad(np.linspace(-1.99, 24, 1000))  # FIXME: magic
        CLs = np.empty_like(alphas)
        CDs = np.empty_like(alphas)
        Cms = np.empty_like(alphas)

        # First, compute the unadjusted global coefficients
        coefs2d = Coefs2D(self.parafoil, self.brake_geo)
        for n, alpha in enumerate(alphas):
            CL, CD, Cm = coefs2d._pointwise_global_coefficients(alpha, delta_B=0)
            CLs[n], CDs[n], Cms[n] = CL, CD, Cm


        # WIP: I'd like to replace the above
        # alphas = coefs2d.alphas
        # CL = coefs2d.CL(alphas)
        # CD = coefs2d.CD(alphas)
        # CM_c4 = coefs2d.CM_c4(alphas)


        # Second, adjust the global coefficients to account for the 3D wing
        mask = (alphas > np.deg2rad(-3)) & (alphas < np.deg2rad(9))
        CL_prime = Polynomial.fit(alphas[mask], CLs[mask], 1)

        # i0 = alphas[np.argmin(np.abs(CLs))]
        i0 = CL_prime.roots()[0]  # Unadjusted global zero-lift angle
        a_prime = CL_prime.deriv()(0.05)  # Preliminary lift-curve slope
        a = a_prime/(1 + a_prime/(np.pi * self.parafoil.geometry.AR))  # Adjusted slope

        # Skew the CL curve using an (approximately?) area-preserving transform
        alphas = np.sqrt((a_prime/a))*(alphas - i0) + i0
        CLs = np.sqrt((a/a_prime))*CLs
        CL = Polynomial.fit(alphas, CLs, 5)

        D0 = self.parafoil.airfoil.coefficients.Cd(alphas)
        e = 0.95  # Efficiency factor
        Di = CL(alphas)**2 / (np.pi * e * self.parafoil.geometry.AR)
        CD = Polynomial.fit(alphas, Di + D0, 5)

        CM = Polynomial.fit(alphas, Cms, 2)

        return CL, CD, CM

    def _pointwise_local_coefficients(self, alpha, delta_B):
        """
        This is my replacement for PFD Sec:4.3.5
        """
        N = 501
        y = np.linspace(-self.parafoil.geometry.b/2,
                        self.parafoil.geometry.b/2, 501)
        dy = self.parafoil.geometry.b/(N - 1)

        Gamma = self.parafoil.geometry.Gamma(y)
        theta = self.parafoil.geometry.ftheta(y)
        c = self.parafoil.geometry.fc(y)

        uL, wL = np.cos(alpha), np.sin(alpha)
        ui = uL
        wi = wL*cos(Gamma)
        alpha_i = np.arctan(wi/ui) + theta

        i0 = self.CL.roots()[0]
        D0 = self.CD(i0)

        numerator = self.CL(alpha)*self.geometry.S
        denominator = trapz((alpha_i - i0)*c/cos(Gamma), dy)
        a_bar = numerator/denominator

        numerator = self.CD(alpha)*self.geometry.S - trapz(D0*c/cos(Gamma), dy)
        Cl = a_bar*(alpha_i - i0)
        denominator = trapz(Cl**2 * c / cos(Gamma), dy)
        D2_bar = numerator/denominator

        self.a_bar = a_bar
        self.D2_bar = D2_bar
        return a_bar, D2_bar

    # FIXME: need to define the _Cl, _Cd, _Cm
    #  * Do I want to stick with the a_bar, D2_bar design, or go ahead and
    #    switch to something simpler?
    #  * How do I want to handle symmetric brakes?
    #     * Split into half-wings and parameterize on the quarter-span alpha?


class Coefs2(CoefficientsEstimator):
    # This is sort of a temporary hack, a partial coefficients estimator that
    # only provides the global coefficients. I'm using it to test my steady-
    # state estimation code (finding alpha_eq).

    def __init__(self, parafoil, brake_geo):
        self.parafoil = parafoil
        self.brake_geo = brake_geo  # FIXME: shouldn't need to keep this around
        self.coefs2d = Coefs2D(parafoil, brake_geo)

        # Compute 25 Polynomial approxmations over the operating brake range
        self.delta_Bs = np.linspace(0, 1, 25)

        # Initialize the global (3D) aerodynamic coefficients
        CLs, CDs, CM_c4s = self._compute_global_coefficients()
        self.CLs = CLs
        self.CDs = CDs
        self.CM_c4s = CM_c4s

    def Cl(self, y, alpha, delta_Bl, delta_Br):
        # FIXME: doesn't support asymmetric braking
        assert delta_Bl == delta_Br
        CL_2d = self.coefs2d.CL(alpha, delta_Bl)
        CL_3d = self.CL(alpha, delta_Bl)
        k = CL_3d/CL_2d
        return k*self.coefs2d.Cl(y, alpha, delta_Bl, delta_Br)

    def Cd(self, y, alpha, delta_Bl, delta_Br):
        raise NotImplementedError

    def Cm(self, y, alpha, delta_Bl, delta_Br):
        raise NotImplementedError


    # FIXME: doesn't do any interpolation between deltas
    #  * Switch to RectBivariateSpline?
    def CL(self, alpha, delta_B):
        di = np.argmin(np.abs(self.delta_Bs - delta_B))
        return self.CLs[di](alpha)

    def CD(self, alpha, delta_B):
        di = np.argmin(np.abs(self.delta_Bs - delta_B))
        return self.CDs[di](alpha)

    def CM_c4(self, alpha, delta_B):
        di = np.argmin(np.abs(self.delta_Bs - delta_B))
        return self.CM_c4s[di](alpha)

    def _compute_global_coefficients(self):
        """
        Fit polynomials to the adjusted global aerodynamic coefficients.

        This procedure is from Paraglider Flight Dynamics, Sec:4.3.4

        FIXME: the alpha range should depend on the airfoil.
        FIXME: currently assumes linear CL, with no expectation of stalls!!
        FIXME: for non-constant-linear airfoils, do these fittings hold?
        FIXME: seems convoluted for what it accomplishes
        """
        _alphas = np.deg2rad(np.linspace(-1.99, 24, 100))  # FIXME: magic

        _CLs, _CDs, _CM_c4s = [], [], []  # Polynomials for each delta_B
        for delta_B in self.delta_Bs:
            CLs = self.coefs2d.CL(_alphas, delta_B)
            CDs = self.coefs2d.CD(_alphas, delta_B)
            CM_c4s = self.coefs2d.CM_c4(_alphas, delta_B)

            # Adjust the global coefficients to account for the 3D wing
            mask = (_alphas > np.deg2rad(-3)) & (_alphas < np.deg2rad(9))
            CL_prime = Polynomial.fit(_alphas[mask], CLs[mask], 1)

            i0 = CL_prime.roots()[0]  # Unadjusted global zero-lift angle
            a_prime = CL_prime.deriv()(0.05)  # Preliminary lift-curve slope
            a = a_prime/(1 + a_prime/(np.pi * self.parafoil.geometry.AR))  # Adjusted slope

            # Skew the CL curve using an (approximately?) area-preserving
            # transform, while maintaing the same zero-lift angle of attack.
            alphas = np.sqrt((a_prime/a))*(_alphas - i0) + i0
            CLs = np.sqrt((a/a_prime))*CLs
            CL = Polynomial.fit(alphas, CLs, 5)

            # D0 = self.parafoil.airfoil.coefficients.Cd(alphas)
            D0 = CDs  # FIXME: correct?
            e = 0.95  # Efficiency factor
            Di = CL(alphas)**2 / (np.pi * e * self.parafoil.geometry.AR)
            CD = Polynomial.fit(alphas, Di + D0, 5)

            CM_c4 = Polynomial.fit(alphas, CM_c4s, 2)

            _CLs.append(CL)
            _CDs.append(CD)
            _CM_c4s.append(CM_c4)

        return _CLs, _CDs, _CM_c4s


# class Anderson(CoefficientsEstimator):
class Anderson:
    """
    An unfinished implementation of Anderson's method.

    Unfortunately, this is for straight wings (no sweep, no dihedral), and
    gives strange results if you try it on a paraglider. For example, it thinks
    that the induced AoA on the outer ~1/4 semispans is negative?

    References
    ----------
    .. [1] J.F. Anderson, "Numerical Lifting Line Theory Applied to Drooped
       Leading-Edge Wings Below and Above Stall", Journal of Aircraft, 1980.

    .. [2] J.F. Anderson, "A numerical nonlinear lifting-line method",
       Fundamentals of Aerodynamics, pp. 465-468, 2016.
    """

    def __init__(self, parafoil, brake_geo):
        self.parafoil = parafoil
        self.brake_geo = brake_geo  # FIXME: shouldn't need to keep this around
        self.coefs2d = Coefs2D(parafoil, brake_geo)

    def _compute_section_coefs(self, alpha, delta_B):
        # Note: this version has been modified to use cosine sampling for `y`

        print("\n\n")
        print("\t\t!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("\t\tWARNING: this does not work for paraglider wings")
        print("\t\t!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("\n\n")

        b = self.parafoil.geometry.b
        S = self.parafoil.geometry.S

        # 1. Divide the wing into k sections (so k+1 sample points on the span)
        K = 25
        t = np.linspace(0, np.pi, K+1)
        dt = np.pi/K
        y = (-b/2) * cos(t)  # Cosine distributed spanwise samples
        c = self.parafoil.geometry.fc(y)
        dihedral = self.parafoil.geometry.Gamma(y)

        # 2. Fit an initial elliptical span-load distribution
        V_inf = 1  # For computing the coefficients, let the airspeed = 1 m/s
        CL_2d = self.coefs2d.CL(alpha, delta_B)
        Gamma0 = 2*V_inf*S*CL_2d/(np.pi*b)  # Central chord circulation

        # Gamma_proposal = Gamma0 * np.sqrt(1 - ((2*y)/b)**2)  # Elliptical
        Gamma_proposal = Gamma0 * np.sin(t)  # Elliptical

        # Should have: trapz(Gamma_proposal*np.sin(t)*(b/S), dt) ~== CL_2d
        tmp = (b/(V_inf*S)) * trapz(Gamma_proposal*np.sin(t), dt)
        print("Check: CL_2d/proposal: {}".format(CL_2d/tmp))

        embed()

        iteration = 0
        while True:  # FIXME: move into a helper function
            print("iteration:", iteration)

            # 3. Compute the alpha_i
            alpha_i = np.empty_like(y)

            # dGamma_dy = np.gradient(Gamma_proposal, dy)

            # gamma_spline = UnivariateSpline(y, Gamma_proposal, k=2)
            # gamma_spline = UnivariateSpline(y, Gamma_proposal, k=4)
            # gamma_spline = CubicSpline(y, Gamma_proposal)
            # dGamma_dy = gamma_spline.derivative()(y)

            # gamma_interp = interp1d(y, Gamma_proposal, kind=3)
            # dGamma_dy = gamma_interp._spline.derivative()(y)

            # gamma_poly = Polynomial.fit(y, Gamma_proposal, 12)
            # dGamma_dy = gamma_poly.deriv()(y)
            gamma_poly = Polynomial.fit(t, Gamma_proposal, 12)
            dGamma_dt = gamma_poly.deriv()(t)

            for n, t_n in enumerate(t):
                denom = cos(t) - cos(t_n)
                denom[n] = 1  # Avoid stupid singularities

                factors = dGamma_dt/denom  # For the integral

                # Avoid singularities where `t_n == t` by using an average,
                if n == 0:
                    # factors[0] = (0 + factors[1])/2
                    factors[0] = -factors[1]
                elif n == K:
                    # factors[K] = (factors[K-1] + 0)/2
                    factors[K] = -factors[K-1]
                else:
                    factors[n] = (factors[n-1] + factors[n+1])/2

                # Account for the `y = (b/2)*cos(t)` substitution
                alpha_i[n] = 1/(4*np.pi*V_inf) * trapz(factors*np.sin(t), dt)
                # FIXME: negative because of dGamma_dt being backwards?

                # print('n:', n)
                # plt.plot(y, factors, marker='.')
                # plt.plot(y, dGamma_dt, marker='.')
                # plt.show()

                # embed()

            if np.any(np.isnan(alpha_i)):
                print("alpha_i has nan's")
                embed()
                input('continue?')

            # 4. Compute alpha_eff
            alpha_eff = alpha - alpha_i
            local_alpha_eff = alpha_eff*cos(dihedral)

            # 5. Compute the effective lift coefficients
            cl = self.coefs2d.Cl(y, local_alpha_eff, delta_B, delta_B)
            cl = cl * cos(dihedral)  # Convert local lift to global lift

            if np.any(np.isnan(cl)):
                print("cl has nan's")
                embed()
                input('continue?')

            if np.any(local_alpha_eff > np.deg2rad(15)):
                print("Large local alpha_eff!")
                embed()
                input("continue?")

            # 6. Use the effective section lift coefficients to rebuild Gamma
            Gamma_new = (1/2)*V_inf*c*cl  # FIXME: dihedral corrections?

            # fig, ax = plt.subplots(3, sharex=True)
            # ax[0].plot(y, np.rad2deg(alpha_i), label='alpha_i')
            # ax[0].plot(y, np.rad2deg(alpha_eff), label='alpha_eff')
            # ax[1].plot(y, cl)
            # ax[2].plot(y, Gamma_proposal, label='Gamma_proposal')
            # ax[2].plot(y, Gamma_new, label='Gamma_new')
            # ax[0].set_xlabel('y')
            # ax[1].set_xlabel('y')
            # ax[2].set_xlabel('y')
            # ax[0].set_ylabel('alpha')
            # ax[1].set_ylabel('cl')
            # ax[2].set_ylabel('Circulation')
            # ax[0].legend()
            # ax[2].legend()
            # ax[0].grid(True)
            # ax[1].grid(True)
            # ax[2].grid(True)
            # plt.show()

            # 7. Combine Gamma_new with the proposal
            D = 0.05
            Gamma = Gamma_proposal + D*(Gamma_new - Gamma_proposal)

            # Repeat until all sections have changed less than 1%
            # err = (Gamma_new - Gamma_proposal)/Gamma_proposal*100
            # if np.all(err < 1):
            #     break
            # else:
            #     print("\rMax error percent: {}".format(max(err)), end="")
            #     # print('.', end='')
            #     continue

            # embed()

            iteration += 1
            if iteration > 250:
                break

            Gamma_proposal = Gamma

        print('\nfinished finding cl and cdi')
        embed()

        # 9. Use the final Gamma (circulation) to compute the section coefs
        cl = 2*Gamma/(V_inf*c)
        cdi = cl*alpha_i

        return cl, cdi


# class Anderson(CoefficientsEstimator):
class Anderson2:
    """
    An unfinished implementation of Anderson's method.

    Unfortunately, this is for straight wings (no sweep, no dihedral), and
    gives strange results if you try it on a paraglider. For example, it thinks
    that the induced AoA on the outer ~1/4 semispans is negative?

    References
    ----------
    .. [1] J.F. Anderson, "Numerical Lifting Line Theory Applied to Drooped
       Leading-Edge Wings Below and Above Stall", Journal of Aircraft, 1980.

    .. [2] J.F. Anderson, "A numerical nonlinear lifting-line method",
       Fundamentals of Aerodynamics, pp. 465-468, 2016.
    """

    def __init__(self, parafoil, brake_geo):
        self.parafoil = parafoil
        self.brake_geo = brake_geo  # FIXME: shouldn't need to keep this around
        self.coefs2d = Coefs2D(parafoil, brake_geo)

    def _compute_section_coefs(self, alpha, delta_B):
        # Note: this version has been modified to use cosine sampling for `y`

        print("\n\n")
        print("\t\t!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("\t\tWARNING: this does not work for paraglider wings")
        print("\t\t!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("\n\n")

        b = self.parafoil.geometry.b
        S = self.parafoil.geometry.S

        # 1. Divide the wing into k sections (so k+1 sample points on the span)
        K = 100
        t = np.linspace(0, np.pi, K+1)
        dt = np.pi/K
        y = (-b/2) * cos(t)  # Cosine distributed spanwise samples
        c = self.parafoil.geometry.fc(y)
        dihedral = self.parafoil.geometry.Gamma(y)

        # 2. Fit an initial elliptical span-load distribution
        V_inf = 1  # For computing the coefficients, let the airspeed = 1 m/s
        CL_2d = self.coefs2d.CL(alpha, delta_B)
        Gamma0 = 2*V_inf*S*CL_2d/(np.pi*b)  # Central chord circulation

        def get_cl2d(alpha, delta_B):
            u, w = cos(alpha), sin(alpha)
            dF, dM = self.coefs2d.section_forces(y, u, 0, w, delta_B, delta_B)
            dFx, dFz = dF[:, 0], dF[:, 2]

            dL = dFx*sin(alpha) - dFz*cos(alpha)
            return dL*2/c  # From dL = (1/2)*rho*(V_inf**2)*c*cl

        cl2d = get_cl2d(alpha, delta_B)
        # Gamma_proposal = Polynomial.fit(t, cl2d*c/2, 10)
        Gamma_proposal = cl2d*c/2

        # Should have: trapz(Gamma_proposal*np.sin(t)*(b/S), dt) ~== CL_2d
        tmp = (b/(V_inf*S)) * trapz(Gamma_proposal*np.sin(t), dt)
        print("Check: CL_2d/proposal: {}".format(CL_2d/tmp))
        # FIXME: it doesn't, because CL_2d uses linear samples in y

        # embed()
        # return

        iteration = 0
        while True:  # FIXME: move into a helper function
            print("iteration:", iteration)

            # 3. Compute the alpha_i
            alpha_i = np.empty_like(y)

            # dGamma_dy = np.gradient(Gamma_proposal, dy)

            # gamma_spline = UnivariateSpline(y, Gamma_proposal, k=2)
            # gamma_spline = UnivariateSpline(y, Gamma_proposal, k=4)
            # gamma_spline = CubicSpline(y, Gamma_proposal)
            # dGamma_dy = gamma_spline.derivative()(y)

            # gamma_interp = interp1d(y, Gamma_proposal, kind=3)
            # dGamma_dy = gamma_interp._spline.derivative()(y)

            # gamma_poly = Polynomial.fit(y, Gamma_proposal, 12)
            # dGamma_dy = gamma_poly.deriv()(y)
            gamma_poly = Polynomial.fit(t, Gamma_proposal, 12)
            dGamma_dt = gamma_poly.deriv()(t)

            for n, t_n in enumerate(t):
                denom = cos(t) - cos(t_n)
                denom[n] = 1  # Avoid stupid singularities

                factors = dGamma_dt/denom  # For the integral

                # Avoid singularities where `t_n == t` by using the "average"
                factors[n] = 0  # FIXME: not sure what he means by "average"
                # if n == 0:
                #     # factors[0] = (0 + factors[1])/2
                #     # factors[0] = -factors[1]
                # elif n == K:
                #     # factors[K] = (factors[K-1] + 0)/2
                #     # factors[K] = -factors[K-1]
                # else:
                #     factors[n] = (factors[n-1] + factors[n+1])/2

                # Account for the `y = (b/2)*cos(t)` substitution
                alpha_i[n] = 1/(4*np.pi*V_inf) * trapz(factors*np.sin(t), dt)
                # FIXME: negative because of dGamma_dt being backwards?

                # print('n:', n)
                # plt.plot(y, factors, marker='.')
                # plt.plot(y, dGamma_dt, marker='.')
                # plt.show()

                # embed()

            if np.any(np.isnan(alpha_i)):
                print("alpha_i has nan's")
                embed()
                input('continue?')

            # 4. Compute alpha_eff
            alpha_eff = alpha - alpha_i
            local_alpha_eff = alpha_eff*cos(dihedral)

            # 5. Compute the effective lift coefficients
            # cl = self.coefs2d.Cl(y, local_alpha_eff, delta_B, delta_B)
            # cl = cl * cos(dihedral)  # Convert local lift to global lift
            cl = get_cl2d(alpha_eff, delta_B)

            if np.any(np.isnan(cl)):
                print("cl has nan's")
                embed()
                input('continue?')

            if np.any(local_alpha_eff > np.deg2rad(15)):
                print("Large local alpha_eff!")
                embed()
                input("continue?")

            # 6. Use the effective section lift coefficients to rebuild Gamma
            Gamma_new = (1/2)*V_inf*c*cl  # FIXME: dihedral corrections?

            # FIXME: does this help?
            Gamma_new[0] = 0
            Gamma_new[-1] = 0


            # fig, ax = plt.subplots(3, sharex=True)
            # ax[0].plot(y, np.rad2deg(alpha_i), label='alpha_i')
            # ax[0].plot(y, np.rad2deg(alpha_eff), label='alpha_eff')
            # ax[1].plot(y, cl)
            # ax[2].plot(y, Gamma_proposal, label='Gamma_proposal')
            # ax[2].plot(y, Gamma_new, label='Gamma_new')
            # ax[0].set_xlabel('y')
            # ax[1].set_xlabel('y')
            # ax[2].set_xlabel('y')
            # ax[0].set_ylabel('alpha')
            # ax[1].set_ylabel('cl')
            # ax[2].set_ylabel('Circulation')
            # ax[0].legend()
            # ax[2].legend()
            # ax[0].grid(True)
            # ax[1].grid(True)
            # ax[2].grid(True)
            # plt.show()

            # 7. Combine Gamma_new with the proposal
            D = 0.05
            Gamma = Gamma_proposal + D*(Gamma_new - Gamma_proposal)

            # Repeat until all sections have changed less than 1%
            # err = (Gamma_new - Gamma_proposal)/Gamma_proposal*100
            # if np.all(err < 1):
            #     break
            # else:
            #     print("\rMax error percent: {}".format(max(err)), end="")
            #     # print('.', end='')
            #     continue

            # embed()
            # input('continue?')

            # print("\nEarly termination at the first iteration\n")
            # break

            iteration += 1
            if iteration > 250:
                break

            Gamma_proposal = Gamma

        print('\nfinished finding cl and cdi')
        embed()

        # 9. Use the final Gamma (circulation) to compute the section coefs
        cl = 2*Gamma/(V_inf*c)
        cdi = cl*alpha_i

        return cl, cdi


class Phillips(CoefficientsEstimator):
    """
    Supposed to work with wings with sweep and dihedral, but much more complex.

    References
    ----------
    .. [1] Phillips and Snyder, "Modern Adaptation of Prandtl’s Classic
       Lifting- Line Theory", Journal of Aircraft, 2000

    .. [2] McLeanauth, "Understanding Aerodynamics - Arguing from the Real
       Physics", p382

    Notes
    -----
    This method does suffer an issue where induced velocity goes to infinity as
    the segment length is decreased. See _[2], section 8.2.3.
    """

    def __init__(self):
        raise NotImplementedError

        # Define the spanwise and nodal and control points
        # NOTE: this is suitable for parafoils, but for wings made of left
        #       and right segments, you should distribute the points across
        #       each span independently. See _[1] ([1] Phillips)
        # FIXME: Phillips indexes the nodal points from zero, and the control
        #        points from 1. Should I do the same?
        K = 50  # The number of bound vortex segments for the entire span
        k = np.arange(K+1)
        b = self.parafoil.geometry.b

        # Nodes are indexex from 0..K+1
        node_y = (-b/2) * np.cos(k * np.pi / K)
        node_x = self.parafoil.geometry.fx(node_y)
        node_z = self.parafoil.geometry.fz(node_y)
        self.nodes = np.c_[node_x, node_y, node_z]

        # Control points  are indexed from 0..K
        cp_y = (-b/2) * (np.cos(np.pi/(2*K) + k[:-1]*np.pi/K))
        cp_x = self.parafoil.geometry.fx(cp_y)
        cp_z = self.parafoil.geometry.fz(cp_y)
        self.cps = np.c_[cp_x, cp_y, cp_z]

        # Define the orthogonal unit vectors for each control point
        # FIXME: these need verification; their orientation in particular
        # FIXME: also, check their magnitudes
        dihedral = self.parafoil.geometry.Gamma(self.cps)
        twist = self.parafoil.geometry.theta(self.cps)  # Angle of incidence
        sd, cd = sin(dihedral), cos(dihedral)
        st, ct = sin(twist), cos(twist)
        self.u_s = np.c_[np.zeros_like(self.cps), cd, sd]  # Spanwise
        self.u_a = np.c_[ct, st*sd, st*cd]  # Chordwise
        self.u_n = np.cross(self.u_a, self.u_s)  # Normal to the span and chord

        assert np.allclose(np.linalg.norm(self.u_s), 1)
        assert np.allclose(np.linalg.norm(self.u_a), 1)
        assert np.allclose(np.linalg.norm(self.u_n), 1)

        # Define the differential areas
        # FIXME: implement


    def find_vortex_strengths(self, V_inf, delta_Bl, delta_Br):
        """

        Parameters
        ----------
        V_inf : array of float, shape (K,) [meters/second]
            Fluid velocity vectors for each section, in body coordinates. This
            is equal to the relative wind "far" from each wing section, which
            is absent of circulation effects.
        delta_Bl : float [percentage]
            The amount of left brake
        delta_Br : float [percentage]
            The amount of right brake
        """


        # 2. Compute the "induced velocity" unit vectors
        #  * ref: Phillips, Eq:6

        # 3. Propose an initial distribution for G
        #  * where `G` is the dimensionless vortex strength vector
        #  * Starting with an elliptical Gamma is fine?
        #  * ref: Phillips Eq:23, where use uses a linearized equation to
        #    suggest an initial distribution for G

        while True:
            # 4. Compute the local fluid velocities
            #  * ref: Hunsaker-Snyder Eq:5
            #  * ref: Phillips Eq:5 (nondimesional version)
            V_tot = V_inf + np.sum(Gamma*inducedVelocities)

            # 5. Compute the section local angle of attack
            #  * ref: Phillips Eq:9 (dimensional) or Eq:12 (dimensionless)
            alpha = arctan(dot(V_tot, self.u_n), dot(V_tot, self.u_a))

            # And lookup the section lift coefficients
            cl = self.coefs2d.Cl(self.cps, alpha, delta_Bl, delta_Br)

            # 7. Compute the 
            #  * ref: Hunsaker-Snyder Eq:11

            # 6. Compute the residual error
            #  * ref: Phillips Eq:15, or Hunsaker-Snyder Eq:8






# noqa: E303
