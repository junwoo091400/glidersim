# import abc

from functools import partial

import numpy as np

from scipy.optimize import minimize_scalar
from scipy.integrate import simps

from IPython import embed

from util import trapz


class ParagliderWing:
    # FIXME: review weight shift and speedbar designs. Why use percentage-based
    #        controls?

    def __init__(self, parafoil, force_estimator, brake_geo, d_riser, z_riser,
                 kappa_s=0):
        """
        Parameters
        ----------
        parafoil : Parafoil
        force_estimator : ForceEstimator
            Calculates the aerodynamic forces and moments on the parafoil
        d_riser : float [percentage]
            The longitudinal distance from the risers to the central leading
            edge, as a percentage of the chord length.
        z_riser : float [meters]
            The vertical distance from the risers to the central chord
        kappa_s : float [meters] (optional)
            The speed bar line length. This corresponds to the maximum change
            in the length of the lines to the leading edge.
        """
        self.parafoil = parafoil
        self.force_estimator = force_estimator(parafoil)
        self.brake_geo = brake_geo
        self.kappa_s = kappa_s  # FIXME: strange notation. Why `kappa`?

        # The ParagliderWing coordinate system is a shifted version of the
        # one defined by the Parafoil. The axes of both systems are parallel,
        # but the origin moves from the central leading edge to the midpoint
        # of the risers.
        self.c0 = parafoil.planform.fc(0)
        foil_x = d_riser * self.c0
        foil_z = -z_riser
        self.LE = np.sqrt(foil_x**2 + foil_z**2)
        self.TE = np.sqrt((self.c0 - foil_x)**2 + foil_z**2)

        # For testing, from a Hook 3
        self.rho_upper = 40/1000  # kg/m2
        self.rho_lower = 35/1000  # kg/m2

    def forces_and_moments(self, V_cp2w, delta_Bl, delta_Br):
        """

        Parameters
        ----------
        V_cp2w : ndarray of floats, shape (K,3) [m/s]
            The relative velocity of each control point vs the fluid
        delta_Bl : float [percentage]
            The amount of left brake
        delta_Br : float [percentage]
            The amount of right brake

        Returns
        -------
        dF, dM : array of float, shape (K,3)
            Forces and moments for each section
        """
        delta = self.brake_geo(self.force_estimator.s_cps, delta_Bl, delta_Br)
        dF, dM = self.force_estimator(V_cp2w, delta)
        return dF, dM

    def foil_origin(self, delta_s=0):
        """
        Compute the origin of the Parafoil coordinate system in ParagliderWing
        coordinates.

        Parameters
        ----------
        delta_s : float or array of float, shape (N,) [percentage] (optional)
            Fraction of maximum speed bar application

        Returns
        -------
        foil_origin : array of float, shape (3,) [meters]
            The offset of the origin of the Parafoil coordinate system in
            ParagliderWing coordinates.
        """
        LE = self.LE - (delta_s * self.kappa_s)
        foil_x = (LE**2 - self.TE**2 + self.c0**2)/(2*self.c0)
        foil_y = 0  # FIXME: not with weight shift?
        foil_z = -np.sqrt(LE**2 - foil_x**2)
        return np.array([foil_x, foil_y, foil_z])

    def alpha_eq(self, delta_B, delta_S):
        """

        Parameters
        ----------
        delta_B : float [percentage]
            Fraction of symmetric braking application
        delta_S : float [percentage]
            Fraction of speed bar application

        Returns
        -------
        alpha_eq : float [radians]
            The equilibrium angle of attack for the given control inputs
        """

        def moment_factor(delta_B, delta_S, alpha):
            raise RuntimeError("FIXME: broken. Wrong implementaion anyway.")
            CL = self.parafoil_coefs.CL(alpha, delta_B)
            CD = self.parafoil_coefs.CD(alpha, delta_B)
            CM_c4 = self.parafoil_coefs.CM(alpha, delta_B)

            Cx = CL*np.sin(alpha) - CD*np.cos(alpha)
            Cz = -CL*np.cos(alpha) - CD*np.sin(alpha)  # FIXME: verify

            MAC = self.parafoil.geometry.MAC
            c = self.parafoil.geometry.fc(0)
            d_cg, h_cg = self.cg_position(delta_S)

            kMy = CM_c4*MAC - Cx*h_cg - Cz*((d_cg - 1/4)*c)

            return np.abs(kMy)

        f = partial(moment_factor, delta_B, delta_S)
        alpha_min, alpha_max = np.deg2rad(-1.5), np.deg2rad(20)  # FIXME: magic
        r = minimize_scalar(f, bounds=(alpha_min, alpha_max), method='Bounded')

        return r.x

    def control_points(self, delta_s=0):
        """
        The Parafoil control points in ParagliderWing coordinates.

        Parameters
        ----------
        delta_s : float or array of float, shape (N,) [percentage] (optional)
            Fraction of maximum speed bar application

        Returns
        -------
        cps : ndarray of floats, shape (K,3) [meters]
            The control points in ParagliderWing coordinates
        """
        cps = self.force_estimator.control_points  # In Parafoil coordinates
        return cps + self.foil_origin(delta_s)  # In Wing coordinates

    # FIXME: moved from foil. Verify and test.
    def surface_distributions(self, delta_s=0):
        """The surface area distributions for computing inertial moments.

        The moments of inertia for the parafoil are the mass distribution of
        the air and wing material. That distribution is typically decomposed
        into the product of volumetric density and volume, but a simplification
        is to calculate the density per unit area.

        FIXME: this description is mediocre.

        Ref: "Paraglider Flight Dynamics", page 48 (56)

        Returns
        ------
        S : 3x3 matrix of float
            The surface distributions, such that `J = (p_w + p_air)*S`
        """
        N = 501
        s = np.cos(np.linspace(np.pi, 0, N))  # -1 < s < 1
        x, y, z = (self.parafoil.c4(s) + self.foil_origin(delta_s)).T
        c = self.parafoil.planform.fc(s)

        Sx = simps((y**2 + z**2)*c, y)
        Sy = simps((3*x**2 - x*c + (7/32)*c**2 + 6*z**2)*c, y)/6
        Sz = simps((3*x**2 - x*c + (7/32)*c**2 + 6*y**2)*c, y)/6
        Sxy = 0
        Sxz = simps((2*x - c/2)*z*c, y)
        Syz = 0

        S = np.array([
            [Sx, -Sxy, -Sxz],
            [-Sxy, Sy, -Syz],
            [-Sxz, -Syz, Sz]])

        return S

    # FIXME: moved from Parafoil. Verify and test.
    def J(self, rho=1.3, N=2000):
        raise NotImplementedError("BROKEN!")
        S = self.geometry.surface_distributions(N=N)
        wing_air_density = rho*self.density_factor
        surface_density = self.wing_density + wing_air_density
        return surface_density * S

    def inertia(self, lobe_args={}, delta_s=0, rho_air=1.2985, N=200):
        """Compute the 3x3 moment of inertia matrix.

        Parameters
        ----------
        lobe_args : dictionary, optional
            FIXME: when is this used? Wouldn't the wing create it?
        delta_s : float [percentage]
            Percentage of speed bar application
        rho_air : float [kg/m^3]
            Volumetric air density of the atmosphere (default: 1.2985, ISA)
        N : integer
            The number of points for integration across the span

        Returns
        -------
        J : 3x3 matrix of float
            The inertia tensor of the wing

                [[ Jxx -Jxy -Jxz]
            J =  [-Jxy  Jyy -Jyz]
                 [-Jxz -Jyz  Jzz]]
        """
        p = self.parafoil.mass_properties(lobe_args, N)

        # Storing this here for now: calculate the total mass and centroid
        # upper_mass = self.rho_upper * p['upper_area']
        # air_mass = rho_air * p['volume']
        # lower_mass = self.rho_lower * p['lower_area']
        # total_mass = upper_mass + air_mass + lower_mass
        # parafoil_cm = (upper_mass * p['upper_centroid'] +
        #                air_mass * p['volume_centroid'] +
        #                lower_mass * p['lower_centroid']) / total_mass

        o = -self.foil_origin(delta_s)  # Stupid origin is `risers->origin`
        Ru = o - p['upper_centroid']
        Rv = o - p['volume_centroid']
        Rl = o - p['lower_centroid']
        Du = (Ru @ Ru) * np.eye(3) - np.outer(Ru, Ru)
        Dv = (Rv @ Rv) * np.eye(3) - np.outer(Rv, Rv)
        Dl = (Rl @ Rl) * np.eye(3) - np.outer(Rl, Rl)

        J_wing = (self.rho_upper * (p['upper_inertia'] + p['upper_area']*Du) +
                  rho_air * (p['volume_inertia'] + p['volume'] * Dv) +
                  self.rho_lower * (p['lower_inertia'] + p['lower_area']*Dl))

        return J_wing
