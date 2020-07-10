import abc

import numpy as np


class Harness(abc.ABC):
    """
    FIXME: docstring
    """

    @abc.abstractmethod
    def control_points(self, delta_w):
        """
        Compute the control points for the harness model dynamics.

        Parameters
        ----------
        delta_w : float [percentage]
            The fraction of weight shift, from -1 (left) to +1 (right)

        Returns
        -------
        r_CP2R : float, shape (K,3) [m]
            Control points relative to the riser midpoint `R`. Coordinates are
            in payload frd, and `K` is the number of control points for the
            harness model.
        """

    @abc.abstractmethod
    def forces_and_moments(self, v_W2h, rho_air):
        """
        Calculate the aerodynamic forces at the control points.

        Parameters
        ----------
        v_W2h : array of float, shape (K,3) [m/s]
            The wind velocity at each of the control points in harness frd.

        Returns
        -------
        dF, dM : array of float, shape (K,3) [N, N m]
            Aerodynamic forces and moments for each control point.
        """

    @abc.abstractmethod
    def mass_properties(self, delta_w):
        """
        FIXME: docstring
        """


class Spherical(Harness):
    """
    Model a harness as a uniform density sphere.

    Coordinates use the front-right-down (frd) convention, with the origin at
    the midpoint of the two riser connections.

    Parameters
    ----------
    mass : float [kg]
        The mass of the harness
    z_riser : float [m]
        The vertical distance from `R` to the harness center.
    S : float [m^2]
        The projected area of the sphere (ie, the area of a circle)

        Typical values for pilot + harness ([1]_):
         * <80kg:           0.5
         * 80kg to 100kg:   0.6
         * >100kg:          0.7

    CD : float
        The isotropic drag coefficient.

        Typical values for pilot + harness ([1]_):
         * Conventional:    0.8
         * Performance:     0.4

    kappa_w : float [m]
        The maximum weight shift distance

    Notes
    -----
    The spherical assumption has several effects:

    * Isotropic drag: the aerodynamic force is the same in all directions, so
      the drag coefficient is a single number. This implies that using the drag
      coefficient for a performance harness (shaped to minimize drag in the
      forward direction) will also reduce the drag from crosswind.

      Also, the aerodynamic moment for a sphere is zero, and since the
      aerodynamic force is computed at the center of mass, the net moment about
      the center of mass is always zero.

    * Isotropic inertia: neglects the fact that pilot will often extend their
      legs forward for aerodynamic efficiency, which should increase the pitch
      and yaw inertia.

    References
    ----------
    .. [1] Benedetti, Diego Muniz. "Paragliders Flight Dynamics". 2012. pg 85
    """

    def __init__(self, mass, z_riser, S, CD, kappa_w):
        self._mass = mass
        self._z_riser = z_riser
        self._S = S
        self._CD = CD
        self._kappa_w = kappa_w  # FIXME: Strange notation to match `kappa_a`

    def control_points(self, delta_w=0):
        return np.array([[0, delta_w * self._kappa_w, self._z_riser]])

    def forces_and_moments(self, v_W2h, rho_air):
        v2 = (v_W2h ** 2).sum()
        u_drag = v_W2h / np.sqrt(v2)  # Drag force unit vector
        dF = 0.5 * rho_air * v2 * self._S * self._CD * u_drag
        dM = np.zeros(3)
        return dF, dM

    def mass_properties(self, delta_w=0):
        # Treats the mass as a uniform density solid sphere
        return {
            "mass": self._mass,
            "cm": self.control_points(delta_w)[0],
            "J": (2 / 5 * self._mass * self._S / np.pi) * np.eye(3),
            "J_apparent": np.zeros((3, 3)),
        }
