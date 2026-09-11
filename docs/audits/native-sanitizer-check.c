/* SPDX-License-Identifier: Apache-2.0 */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "../../native/eq.c"
#include "../../native/fft.c"
int main(void) {
    for (size_t n=2;n<=16384;n*=2) {
        FFT *fft=eqxear_fft_new(n); assert(fft);
        double *in=calloc(n,sizeof(double)), *out=calloc(n+2,sizeof(double));
        for (size_t i=0;i<n;i++) in[i]=sin(i*.7);
        eqxear_fft_execute(fft,in,out);
        for (size_t i=0;i<n+2;i++) assert(isfinite(out[i]));
        eqxear_fft_free(fft); free(in); free(out);
    }
    const LV2_Descriptor *d=lv2_descriptor(0);
    LV2_Handle eq=d->instantiate(d,48000,NULL,NULL); assert(eq);
    float samples[4][512]={{0}}, controls[35]={0};
    for(int ch=0;ch<4;ch++) d->connect_port(eq,ch,samples[ch]);
    for(int i=0;i<35;i++) d->connect_port(eq,i+4,controls+i);
    uint32_t seed=27;
    for(int block=0;block<2000;block++) {
        for(int b=0;b<8;b++) {
            seed=1664525*seed+1013904223;
            controls[3+b*4]=seed%4;
            controls[4+b*4]=20+(seed%19981);
            controls[5+b*4]=(int)(seed%241)/10.0f-12;
            controls[6+b*4]=.3f+(seed%971)/100.0f;
        }
        for(int i=0;i<512;i++) samples[0][i]=samples[1][i]=.001f*sin(i*.31);
        d->run(eq,512);
        for(int i=0;i<512;i++) assert(isfinite(samples[2][i])&&isfinite(samples[3][i]));
    }
    d->cleanup(eq);
    puts("PASS: native FFT sizes 2..16384 and DSP 2000 random control transitions under ASan/UBSan");
}
