import numpy as np
from numpy import arctan


class Airfoil:
    @property
    def t(self):
        # ref PFD 48 (46)
        """Maximum airfoil thickness as a percentage of chord length"""
        raise NotImplementedError("Airfoil is a base class")

    def yc(self, x):
        """Compute the y-coordinate of the mean camber line

        Parameters
        ----------
        x : float
            Position on the chord line, where `0 < x < chord`
        """
        raise NotImplementedError("Airfoil is a base class")

    def yt(self, x):
        """Airfoil thickness, perpendicular to the camber line

        Parameters
        ----------
        x : float
            Position on the chord line, where `0 < x < chord`
        """
        raise NotImplementedError("Airfoil is a base class")

    def fE(self, x):
        """Upper camber line corresponding to the point `x` on the chord

        Parameters
        ----------
        x : float
            Position on the chord line, where `0 < x < chord`
        """
        raise NotImplementedError("Airfoil is a base class")

    def fI(self, x):
        """Lower camber line corresponding to the point `x` on the chord

        Parameters
        ----------
        x : float
            Position on the chord line, where `0 < x < chord`
        """
        raise NotImplementedError("Airfoil is a base class")


class NACA4(Airfoil):
    """Airfoil geometry using a NACA4 parameterization"""

    def __init__(self, code, chord=1):
        """
        Generate a NACA4 airfoil

        Parameters
        ----------
        code : integer
            The 4-digit NACA code
        chord : float
            The length of the chord
        """
        self.chord = chord
        self.code = code
        self.m = (code // 1000) / 100       # Maximum camber
        self.p = ((code // 100) % 10) / 10  # location of max camber
        self.tcr = (code % 100) / 100       # Thickness to chord ratio
        self.pc = self.p * self.chord

    @property
    def t(self):
        return (self.code % 100) / 100

    def yc(self, x):
        m = self.m
        c = self.chord
        p = self.p
        pc = self.pc

        x = np.asarray(x)

        if np.any(x < 0) or np.any(x > c):
            raise ValueError("x must be between 0 and the chord length")

        f = x <= pc  # Filter for the two cases, `x <= pc` and `x > pc`
        cl = np.empty_like(x)
        cl[f] = (m/p**2)*(2*p*(x[f]/c) - (x[f]/c)**2)
        cl[~f] = (m/(1-p)**2)*((1-2*p) + 2*p*(x[~f]/c) - (x[~f]/c)**2)
        return cl

    def yt(self, x):
        t = self.tcr

        x = np.asarray(x)
        if np.any(x < 0) or np.any(x > self.chord):
            raise ValueError("x must be between 0 and the chord length")

        return 5*t*(.2969*np.sqrt(x) - .126*x - .3516*x**2 +
                    .2843*x**3 - .1015*x**4)

    def _theta(self, x):
        """Angle of the mean camber line

        Parameters
        ----------
        x : float
            Position on the chord line, where `0 < x < chord`
        """
        m = self.m
        p = self.p
        c = self.chord
        pc = self.pc

        x = np.asarray(x)
        assert np.all(x >= 0) and np.all(x <= self.chord)

        f = x <= pc  # Filter for the two cases, `x <= pc` and `x > pc`
        dyc = np.empty_like(x)
        dyc[f] = (2*m/p**2)*(p - x[f]/c)
        dyc[~f] = (2*m/(1-p)**2)*(p - x[~f]/c)

        return arctan(dyc)

    def fE(self, x):
        x = np.asarray(x)
        if np.any(x < 0) or np.any(x > self.chord):
            raise ValueError("x must be between 0 and the chord length")

        theta = self._theta(x)
        yt = self.yt(x)
        yc = self.yc(x)
        return np.c_[x - yt*np.sin(theta), yc + yt*np.cos(theta)]

    def fI(self, x):
        x = np.asarray(x)
        if np.any(x < 0) or np.any(x > self.chord):
            raise ValueError("x must be between 0 and the chord length")

        theta = self._theta(x)
        yt = self.yt(x)
        yc = self.yc(x)
        return np.c_[x + yt*np.sin(theta), yc - yt*np.cos(theta)]
