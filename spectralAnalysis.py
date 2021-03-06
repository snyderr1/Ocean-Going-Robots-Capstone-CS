#Ross Snyder, Oregon State University 2021
#Reference: Analyzing Multidirectional Spectra: a Tentative Classification of Available Methods, Benoit 1997
import numpy as np
import pandas as pd
import scipy as sp
import argparse
import sys
from scipy import signal
from scipy import fft
from scipy import optimize
   
#This function calculates PSD's and Fourier Coeffecients for Wave Spectra using numpys welch method
#
#   Inputs: Pandas.DataFrame, sampling rate = fs, segment length (welch) = segLength, one vs two-sided = sided, fft scale (density vs spectrum)
#
#   Outputs: Pandas.DataFrame
#
def displacementToWelch(df:pd.DataFrame, method:str, fs:int, wind:list, segLength:int, sided:bool, scale:list) -> pd.DataFrame:

    #Calculate displacement series
    xSeries = 1 * mkSeries(df, "x")
    ySeries = -1 * mkSeries(df, "y")
    zSeries = mkSeries(df, "z")
    dt = 1/fs
    
    #Calculate psd 
    Ds = seriesToCrossSpectrum(zSeries, xSeries, ySeries, fs, wind, segLength, sided, scale)
    #Calculate the inverse of the cross spectrum (used for imlm)
    Gmn = zxyMatrixInverse(Ds)
    #probably a better way to do this, but we just need the frequency.
    xF, _ = signal.welch(xSeries, fs, window=wind, nperseg=segLength, return_onesided=sided, scaling=scale, detrend=False)

    rf = pd.DataFrame({'freq':xF[1:]})

    #Save the co and quadrature spectra:
    #seriestToCrossSpectrum returns a 3x3 matrix of cross spectrums (zz, zx, zy) = ((0, 0), (0, 1), etc
    rf['Czz'] = np.real(Ds[0][0])
    rf['Cxx'] = np.real(Ds[1][1])
    rf['Cyy'] = np.real(Ds[2][2])
    rf['Cxy'] = np.real(Ds[1][2])
    rf['Qzx'] = np.imag(Ds[0][1])
    rf['Qzy'] = np.imag(Ds[0][2])
    #only resolve freq greater than 0
    rf = rf[rf.freq > 0]
    d = 60

    #create freq and direction matricies for linalg
    freq = rf.freq.to_numpy()
    theta = np.radians(np.arange(0, 360+1, 5))
    (freqG, thetaG) = np.meshgrid(freq, theta)

    rf['a0'] = rf.Czz
    rf['a1'], rf['b1'] = firstOrderFourier(rf.Qzx, rf.Cxx, rf.Cyy, rf.Czz, rf.Qzy)
    rf['a2'], rf['b2'] = secondOrderFourier(rf.Cxx, rf.Cyy, rf.Czz, rf.Cxy)

    #wave number from dispersion relation
    rf['k'] = waveNum(rf.freq)
    k = rf.k.to_numpy()
    if(method == 'mlm'):
        estimate = mlmEstimate(Gmn, thetaG, k, d)
    elif(method == 'imlm'):
        #gradient descent convergence variables
        #for cdip= 2.5/10
        beta =  2.5
        gamma = 10
        mlm = mlmEstimate(Gmn, thetaG, k, d)
        estimate = imlmEstimate(Ds, freq, theta, d, k, beta, gamma)
    elif(method == "mem"):
        estimate = bettermem(rf, Ds, theta, freq)
    #mem = memEstimate(rf, thetaG, k, d)
    
    #estimate = np.transpose(estimate)
    

    #curr = imlmEstimate(mlm, rf.Czz, freq, theta, d, beta, gamma)
    #mlm, imlm, and mem give us more accurate estimations using the previously calculated cross spectral matrix.
    #so we can "recalculate" our cross spectral matrix to give us more accurate fourier coeffecients/wave parameters
    newst = spectrumToCrossSpectrum(np.array(rf.Czz) * estimate, k, theta, d, freq)
    Cxx = np.real(newst[1][1])
    Cyy = np.real(newst[2][2])
    Czz = np.real(newst[0][0])
    Cxy = np.real(newst[1][2])
    Qzx = np.imag(newst[0][1])
    Qzy = np.imag(newst[0][2])

    # rf['Czz'] = Czz
    # rf['Cxx'] = Cxx
    # rf['Cyy'] = Cyy
    # rf['Cxy'] = Cxy
    # rf['Qzx'] = Qzx
    # rf['Qzy'] = Qzy

    
    # #Calculate Fourier Coeffecients
    # rf['a0'] = Czz
    # rf['a1'], rf['b1'] = firstOrderFourier(Qzx, Cxx, Cyy, Czz, Qzy)
    # rf['a2'], rf['b2'] = secondOrderFourier(Cxx, Cyy, Czz, Cxy)

    kFactor = .2 #mlm estimate is given as kappa/mlm. not sure how to calculate kappa, but it should be around .002. no effect on direction.

    return rf, estimate

#This function calculates estimates the directional spectrum using the maximum likelihood method as described in Benoit (1997)
#
#   Inputs: Ds = Cross spectrum, thetaG = directional grid, k =  wavenumber, d = water depth
#
#   Outputs: np.array estimate of the mlm
#
def mlmEstimate(Ds:np.array, thetaG:list, k:int, d:float):
    #Transfer functions given by benoit
    numerator = k * (d + .5)
    denom = k * d
    if(k.any() * d > 15):
        hxy = 1j * k**0
        hz = k**0 
    else:
        hxy = 1j* np.cosh(numerator)/np.sinh(denom)
        hz = np.sinh(numerator)/np.sinh(denom)  
    hmatrix = [hz, hxy, -hxy] # matrix operations
    
    alpha = lambda f : [1, np.cos(f), -np.sin(f)]
    mlmsum = 0
    mlm = []
    #print(np.shape(thetaG))
    for f in thetaG: #from benoit, not sure how to translate the conditions for alpha and beta into matrix operations...
        mlmsum = 0
        for m in range(0, 3):
            for n in range(0, 3):
                mlmsum += np.real((hmatrix[m] * alpha(f)[m]) *  Ds[m][n] * np.conj(alpha(f)[n] * hmatrix[n]))
        mlm.append(mlmsum)
    return 1/np.array(mlm)

#This function calculates estimates the directional spectrum using the maximum entropy method as described in Benoit (1997)
#
#   Inputs: firstFive = first five estimate of the DS, thetaG = directional grid, k =  wavenumber, d = water depth
#
#   Outputs: np.array estimate of the mem
#
def memEstimate(firstFive:pd.DataFrame, thetaG:list, k:float, d:float):
    i = 1j
    c1 = firstFive.a1 + (i * firstFive.b1)
    c2 = firstFive.a2 + (i * firstFive.b2)
    c1Conjugate = np.conjugate(c1)
    c2Conjugate = np.conjugate(c2) 
    F1 = (c1 - c2*c1Conjugate)/(1-np.abs(c1)**2)
    F2 = c2 - (c1 * F1)
    mem = []
    for delta in thetaG:
        numerator = 1 - (F1 * c1Conjugate) - (F2 * c2Conjugate)
        denom = np.abs(1 - F1*(np.cos(delta) - i * np.sin(delta)) - F2 * (np.cos(2*delta) - i * np.sin(2*delta)))**2
        mem.append(1/(2*np.pi) * (numerator/denom))
    return np.array(np.real(mem))

#This function calculates estimates the directional spectrum using the improved maximum entropy method as described in Benoit (1997)
#WIPWIPWIP
#   Inputs: firstFive = first five estimate of the DS, thetaG = directional grid, k =  wavenumber, d = water depth
#
#   Outputs: np.array estimate of the mem
#
def bettermem(ff:pd.DataFrame, cross:list, thetaG:list, freqG:list):
    curr=[]
    estimate = []
    result = []
    def fun(x, theta, y):
        #print(theta)
        dat = np.exp(-x[0]-x[1]*np.cos(theta)-x[2]*np.sin(theta)-x[3]*np.cos(2*theta)-x[4]*np.sin(2*theta))
        a1 = np.trapz(dat * np.cos(theta), x=theta, axis=0)
        b1 = np.trapz(dat * np.sin(theta), x=theta, axis=0)
        a2 = np.trapz(dat * np.cos(2*theta), x=theta, axis=0)
        b2 = np.trapz(dat * np.sin(2*theta), x=theta, axis=0)
        coef = [1, a1, b1, a2, b2]
        print(coef)
        return np.sqrt((np.array(coef) - np.array(y))**2)
    for theta in thetaG:
        for i in range(len(freqG)):
            y = [0, 0, 0, 0, 0]
            best = [0, 0, 0, 0, 0]
            y[0] = ff.a0[i]
            y[1] = ff.a1[i]
            y[2] = ff.b2[i]
            y[3] = ff.a2[i]
            y[4] = ff.b2[i]
            #print(np.shape(cross[:,:,i]))
            #guess = (sp.linalg.orth(sp.linalg.eig(cross[:,:,i])[1]))
            #print(guess)
            #best[0] = cross[0][0][i]
            #best[1], best[2] = firstOrderFourier(cross[0][1][i], cross[1][1][i], cross[2][2][i], best[0], cross[0][2][i])
            #best[3], best[4] = secondOrderFourier(cross[1][1][i], cross[2][2][i], best[0], cross[1][2][i])
            #print(best)
            result = optimize.least_squares(fun, [1, 1, 1, 1, 1], method="lm", args=(thetaG, y))
            curr.append(np.exp(-result.x[0]-result.x[1]*np.cos(theta)-result.x[2]*np.sin(theta)-result.x[3]*np.cos(2*theta)-result.x[4]*np.sin(2*theta)))
            #print(np.shape(result.x))
        estimate.append(curr)
        curr = []
        #print(np.shape(estimate))
    return np.array(estimate)
    

        





    


#This function calculates estimates the directional spectrum using the iterative maximum likelihood method as described in Benoit (1997)
#   wipwipwip
#   Inputs: initialEstimate = mlm estimate of the DS, initialCzz = zz cross specatrum from mlm estimate, freq = frequency domain, theta = directions, d = water depth
#           betaHP/gammaHP = gradient descent parameters
#   Outputs: np.array estimate of the imlm
#
def imlmEstimate(Gmn:list, freq:list, theta:list, d:float, k:list, betaHP:int, gammaHP:int):
    k = waveNum(freq)
    (freqG, thetaG) = np.meshgrid(freq, theta)
    initialEstimate = mlmEstimate(zxyMatrixInverse(Gmn), thetaG, k, d)
    E = Gmn[0][0]#np.array(spectrumToCrossSpectrum(initialEstimate, k, theta, d, freq)[0][0])
    prev = 0
    curr = initialEstimate
    for i in range(20): #iterative mlm from benoit (1984)
        crossEstimate = spectrumToCrossSpectrum(E*curr, k, theta, d, freq) #calculate the cross spectra of the new estimate
        E = crossEstimate[0][0]
        lamb = initialEstimate - prev
        eFactor = (np.abs(lamb)**(betaHP + 1.0))/(lamb * gammaHP)
        #curr = prev + eFactor #adjust the mlm estimate 
        curr = prev + eFactor#mlmEstimate(zxyMatrixInverse(crossEstimate), thetaG, k, d) + eFactor #calculate a new estimate using the estimated spectra
        prev = curr

    return curr
    

#Calculate the cross spectrum from a directional spectrum: used for iterative MLM
def spectrumToCrossSpectrum(S:np.array, k:np.array, theta:np.array, d:float, freq:np.array):

    numerator = k * (d + .5)
    denom = k * d
    if(k.any() * d > 15):
        hxy = 1j * k**0
        hz = k**0 
    else:
        hxy = 1j* np.cosh(numerator)/np.sinh(denom)
        hz = np.sinh(numerator)/np.sinh(denom)  
    hmatrix = [hz, hxy, -hxy] # matrix operations

    Gzz = np.trapz((hmatrix[0] * np.conjugate(hmatrix[0]) * S), x=theta, axis=0)
    Gzx = np.trapz((hmatrix[0] * np.conjugate(hmatrix[1]) * S), x=theta, axis=0)
    Gzy = np.trapz((hmatrix[0] * np.conjugate(hmatrix[2]) * S), x=theta, axis=0)

    Gxz = np.trapz((hmatrix[1] * np.conjugate(hmatrix[0]) * S), x=theta, axis=0)
    Gxx = np.trapz((hmatrix[1] * np.conjugate(hmatrix[1]) * S), x=theta, axis=0)
    Gxy = np.trapz((hmatrix[1] * np.conjugate(hmatrix[2]) * S), x=theta, axis=0)

    Gyz = np.trapz((hmatrix[2] * np.conjugate(hmatrix[0]) * S), x=theta, axis=0)
    Gyx = np.trapz((hmatrix[2] * np.conjugate(hmatrix[1]) * S), x=theta, axis=0)
    Gyy = np.trapz((hmatrix[2] * np.conjugate(hmatrix[2]) * S), x=theta, axis=0)

    G = np.array([[Gzz, Gzx, Gzy], [Gxz, Gxx, Gxy], [Gyz, Gyx, Gyy]])
 
    return G 

# This function calculates the cross spectral matrix (Benoit)
#
#   Inputs: z, y, x time series, desired window, length of welches method segments, one or two sided, "density" or "spectrum" scale
#
#   Outputs: H
# 
def seriesToCrossSpectrum(z:list, x:list, y:list, fs:int, wind:list, segLength:int, sided:bool, scale:list) -> list:
 
    #calculate each element of the matrix
    Gzz = trim(signal.csd(z, z, fs, window=wind, nperseg=segLength, return_onesided=sided,  scaling=scale)[1])
    Gzx = trim(signal.csd(z, x, fs, window=wind, nperseg=segLength, return_onesided=sided,  scaling=scale)[1])
    Gzy = trim(signal.csd(z, y, fs, window=wind, nperseg=segLength, return_onesided=sided,  scaling=scale)[1])

    Gxz = trim(signal.csd(x, z, fs, window=wind, nperseg=segLength, return_onesided=sided,  scaling=scale)[1])
    Gxx = trim(signal.csd(x, x, fs, window=wind, nperseg=segLength, return_onesided=sided,  scaling=scale)[1])
    Gxy = trim(signal.csd(x, y, fs, window=wind, nperseg=segLength, return_onesided=sided,  scaling=scale)[1])

    Gyz = trim(signal.csd(y, z, fs, window=wind, nperseg=segLength, return_onesided=sided,  scaling=scale)[1])
    Gyx = trim(signal.csd(y, x, fs, window=wind, nperseg=segLength, return_onesided=sided,  scaling=scale)[1])
    Gyy = trim(signal.csd(y, y, fs, window=wind, nperseg=segLength, return_onesided=sided,  scaling=scale)[1])
    #build the np array
    G = np.array([[Gzz, Gzx, Gzy], [Gxz, Gxx, Gxy], [Gyz, Gyx, Gyy]])
    return G

# This function calculates the cross spectral matrix (Benoit) using rfft. Experimental, don't use this.
#
#   Inputs: z, y, x time series, desired window, length of welches method segments, one or two sided, "density" or "spectrum" scale
#
#   Outputs: H
# 
def Gmnx(z:list, x:list, y:list, dt:int, N:int) -> list:

    Gzz = np.real(z * np.conjugate(z)  / (N * dt))
    Gzx = np.imag(z * np.conjugate(x)  / (N * dt))
    Gzy = np.imag(z * np.conjugate(y)  / (N * dt))

    Gxz = np.imag(x * np.conjugate(z)  / (N * dt))
    Gxx = np.real(x * np.conjugate(x)  / (N * dt))
    Gxy = np.real(x * np.conjugate(y)  / (N * dt))

    Gyz = np.imag(y * np.conjugate(z)  / (N * dt))
    Gyx = np.imag(y * np.conjugate(x)  / (N * dt))
    Gyy = np.real(y * np.conjugate(y)  / (N * dt))

    G = np.array([[Gzz, Gzx, Gzy], [Gxz, Gxx, Gxy], [Gyz, Gyx, Gyy]])

    return G 

# This function calculates the inverse cross spectral matrix (Benoit)
#
#   Inputs: z, y, x time series, desired window, length of welches method segments, one or two sided, "density" or "spectrum" scale
#
#   Outputs: H
# 
def zxyMatrixInverse(G:list):
    Gv = np.zeros_like(G)
    for i in range(G.shape[-1]):
        Gv[:,:,i] = np.linalg.pinv(G[:,:,i])
    return Gv

#This function computes a series from a DataFrame.
#
#   Inputs: Pandas.DataFrame with fields time and "target". 
#
#   Outputs: Pandas.DataFrame of both fields.
#todo: convert accelerations into displacment
def mkSeries(df:pd.DataFrame, target:str) -> list:
    series = np.array(df[target])
    return series
    
#This function calculates a0, the first fourier coeff.
#
#   Inputs: Surface elevation frequency spectrum
#
#   Outputs: a0
# 
def zeroOrderFourier(spectrum:list) -> list:
    return spectrum

#This function calculates a1, b1, the second set of fourier coeffs.
#
#   Inputs: 2 co/quad spectrums and a list of respective wave numbers. Must all be the same size.
#
#   Outputs: a1, b1
# 
def firstOrderFourier(zx:list, xx:list, yy:list, zz:list, zy:list) -> list:
    a1 = 1 * zx/np.sqrt((xx + yy) * zz)
    b1 = 1 * zy/np.sqrt((xx + yy) * zz)
    return a1, b1

#(NOAA) This function calculates a2, b2, the first fourier coeff.
#
#   Inputs: 3 co/quad spectrums and a list of respective wave numbers. Must all be the same size.
#
#   Outputs: a2, b2
# 
def secondOrderFourier(xx:list, yy:list, zz:list, xy:list) -> list:
    a2 = (xx-yy)/(xx + yy)
    b2 = (2 * xy)/(xx + yy)
    #a2 = (xx-yy)/(xx+yy)
    #b2 = (2 * xy)/(xx+yy)
    return a2, b2

#This function estimates the wavenumber from the frequency using the deep water dispersion relation:
#https://en.wikipedia.org/wiki/Dispersion_(water_waves)#Dispersion_relation -> see the third column for "dispersion relation"
#
#   Inputs: list of frequencies. sorted or unsorted.
#
#   Outputs: list of wave numbers. each wave number corresponds to the freq at the same index, ie k[1] is the wave number for freq[1]
# 
def waveNum(freq:list) -> list:
    #todo: find numpy/scikit gravity constant
    wL = (2 * np.pi * freq)**2/9.86
    return np.array(wL)

# This function trims negative and the zero frequencies
#
#   Inputs: t spectrum
#
#   Outputs: t spectrum
# 
def trim(t:list) -> list:
    t = t[1:]
    return t

#This function calculates PSD's and Fourier Coeffecients for Wave Spectra using numpys rfft function: unfinished
#
#   Inputs: Pandas.DataFrame, sampling rate = fs, segment length (welch) = segLength, one vs two-sided = sided, fft scale (density vs spectrum)
#
#   Outputs: Pandas.DataFrame
#
# todo: convert from accel first, figure out format after export from ECE team
def displacementToRfft(df:pd.DataFrame, fs:int, nseg:int) -> pd.DataFrame:

    print("Use displacementToWelch. No guarentees on the results of displacementToRfft")
    #Calculate displacement series
    xSeries = 1 * mkSeries(df, "x")
    ySeries = -1 * mkSeries(df, "y")
    zSeries = mkSeries(df, "z")
    dt = 1/fs
    
    rf = pd.DataFrame({'freq':np.fft.rfftfreq(len(zSeries), dt)}) 
    N = len(rf.freq) -1
    
    #Calculate psd

    rf["X"] = dt * np.fft.rfft(xSeries) # FFT of x position, m/Hz
    rf["Y"] = dt * np.fft.rfft(ySeries) # FFT of y position, m/Hz
    rf["Z"] = dt * np.fft.rfft(zSeries) # FFT of z position, m/Hz

    #Calculate the co and quadrature spectra
    #discard the frequency, values are the same for our given fs and nperseg
    rf['Cxx'] = np.real(rf.X * np.conjugate(rf.X))  / (N * dt)
    rf['Cyy'] = np.real(rf.Y * np.conjugate(rf.Y))  / (N * dt)
    rf['Czz'] = np.real(rf.Z * np.conjugate(rf.Z))  / (N * dt)

    rf["Qzx"] = np.imag(rf.Z * np.conjugate(rf.X)) / (N) # m^2/Hz
    rf["Qzy"] = np.imag(rf.Z * np.conjugate(rf.Y)) / (N) # m^2/Hz
    rf["Cxy"] = np.real(rf.X * np.conjugate(rf.Y)) / (N) # m^2/Hz


    #Calculate Fourier Components
    
    rf['a0'] = zeroOrderFourier(rf['Czz'])
    rf['a1'], rf['b1'] = firstOrderFourier(rf['Qzx'], rf['Cxx'], rf['Cyy'], rf['Czz'], rf['Qzy'])
    rf['a2'], rf['b2'] = secondOrderFourier(rf['Cxx'], rf['Cyy'], rf['Cyy'], rf['Cxy'])

    
    #only resolve freq greater than 0 and less than .6
    rf = rf[rf.freq > 0]
    d = 571 #depth
    #create freq and direction matricies for linalg
    freq = rf.freq.to_numpy()
    theta = np.radians(np.arange(0, 360+1, 5))
    (freqG, thetaG) = np.meshgrid(freq, theta)
    
    #inverse of the elements of the cross spectral matrix
    Ds = Gmnx(rf.Z, rf.X, rf.Y, dt, N)

    #wave number from dispersion relation
    rf['k'] = waveNum(rf.freq)
    k = rf.k.to_numpy()

    mlm = mlmEstimate(Ds, thetaG, k, d)
    return rf, mlm


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', type=str, metavar ="data.csv", required=True, 
        help = "File name of the csv data to read")
    parser.add_argument('sample', type=float, nargs="?", metavar ="fs", default="10",
        help = "samplerate")
    #parser.add_argument('window', type=str, nargs="?", metavar ="wind", default="han",
    #    help = "Windowing function to apply")
    #parser.add_argument('nseg', type=int, nargs="?", metavar ="nseg", default=1200, 
    #    help = "Number of samples per segment for welch's method")
    args = parser.parse_args()

    try:
        fp = open(args.csv, 'r')
    except FileNotFoundError:
        raise Exception("No CSV file with the given name found.") from None

    df = pd.DataFrame(pd.read_csv(fp))
 
    #print(np.fft.rfft())
    #first parameter = dataframe of displacements or accelerations
    #second parameter = fft method: rfft or welch
    #third parameter = dsf estimation method
    #fourth parameter = sample rate
    ff, sp = displacementToWelch(df, "mlm", 1.28, "boxcar", np.floor(len(df)/8), True, "density")
    print(ff)