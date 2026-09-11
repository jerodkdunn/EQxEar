/* SPDX-License-Identifier: Apache-2.0 */
/* EQxEar's radix-2 FFT. Original implementation; no external FFT dependency.
 * Forward, unnormalized transform of real input into interleaved complex bins.
 * Each instance owns its scratch space and can execute independently.
 */
#include <math.h>
#include <stddef.h>
#include <stdlib.h>

typedef struct {
    size_t n;
    size_t *reverse;
    double *real, *imag, *cosine, *sine;
} FFT;

void eqxear_fft_free(FFT *fft) {
    if (!fft) return;
    free(fft->reverse);
    free(fft->real); free(fft->imag);
    free(fft->cosine); free(fft->sine);
    free(fft);
}

FFT *eqxear_fft_new(size_t n) {
    if (n < 2 || n > (1u << 20) || (n & (n-1))) return NULL;
    FFT *fft = calloc(1, sizeof(*fft));
    if (!fft) return NULL;
    fft->n = n;
    fft->reverse = malloc(n * sizeof(size_t));
    fft->real = malloc(n * sizeof(double));
    fft->imag = malloc(n * sizeof(double));
    fft->cosine = malloc(n/2 * sizeof(double));
    fft->sine = malloc(n/2 * sizeof(double));
    if (!fft->reverse || !fft->real || !fft->imag || !fft->cosine || !fft->sine) {
        eqxear_fft_free(fft);
        return NULL;
    }
    for (size_t i=0; i<n; ++i) {
        size_t value=i, reversed=0;
        for (size_t bit=n; bit>1; bit>>=1) {
            reversed=(reversed<<1) | (value&1);
            value>>=1;
        }
        fft->reverse[i]=reversed;
    }
    const double tau=2*acos(-1.0);
    for (size_t k=0; k<n/2; ++k) {
        fft->cosine[k]=cos(tau*k/n);
        fft->sine[k]=-sin(tau*k/n);
    }
    return fft;
}

void eqxear_fft_execute(FFT *fft, const double *input, double *output) {
    size_t n=fft->n;
    for (size_t i=0; i<n; ++i) {
        fft->real[i]=input[fft->reverse[i]];
        fft->imag[i]=0;
    }
    for (size_t width=2; width<=n; width<<=1) {
        size_t half=width/2, stride=n/width;
        for (size_t base=0; base<n; base+=width) {
            for (size_t j=0; j<half; ++j) {
                size_t a=base+j, b=a+half, k=j*stride;
                double re=fft->real[b]*fft->cosine[k]-fft->imag[b]*fft->sine[k];
                double im=fft->real[b]*fft->sine[k]+fft->imag[b]*fft->cosine[k];
                fft->real[b]=fft->real[a]-re;
                fft->imag[b]=fft->imag[a]-im;
                fft->real[a]+=re;
                fft->imag[a]+=im;
            }
        }
    }
    for (size_t k=0; k<=n/2; ++k) {
        output[2*k]=fft->real[k];
        output[2*k+1]=fft->imag[k];
    }
}
