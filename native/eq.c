/* SPDX-License-Identifier: Apache-2.0 */
/* EQxEar v2. Eight stereo RBJ biquads with a 10 ms control ramp.
 * All storage is allocated at instantiate time. No locks or allocation in run.
 */
#include <lv2/core/lv2.h>
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BANDS 8
#define CONTROLS 35
#define PORTS (4+CONTROLS)
#define URI "urn:eqxear:v2:eq"

typedef struct { double b0,b1,b2,a1,a2; } Coeff;
typedef struct { double x1,x2,y1,y2; } History;
typedef struct {
    float *ports[PORTS];
    double rate, gain, target_gain;
    Coeff coeff[BANDS], target[BANDS];
    History history[2][BANDS];
    float last[CONTROLS];
    unsigned remaining;
    int initialized;
} EQ;

static Coeff identity(void) { return (Coeff){1,0,0,0,0}; }
static double clamp(double x,double lo,double hi) { return fmax(lo,fmin(hi,x)); }
static Coeff coefficients(int type,double frequency,double gain,double q,double rate) {
    if (type==0 || fabs(gain)<1e-12) return identity();
    double w=2*M_PI*clamp(frequency,20,rate*.49)/rate;
    double a=pow(10,gain/40),c=cos(w),alpha=sin(w)/(2*q),t=2*sqrt(a)*alpha;
    double b0,b1,b2,a0,a1,a2;
    if (type==1) {
        b0=1+alpha*a; b1=-2*c; b2=1-alpha*a;
        a0=1+alpha/a; a1=-2*c; a2=1-alpha/a;
    } else if (type==2) {
        b0=a*((a+1)-(a-1)*c+t); b1=2*a*((a-1)-(a+1)*c); b2=a*((a+1)-(a-1)*c-t);
        a0=(a+1)+(a-1)*c+t; a1=-2*((a-1)+(a+1)*c); a2=(a+1)+(a-1)*c-t;
    } else {
        b0=a*((a+1)+(a-1)*c+t); b1=-2*a*((a-1)+(a+1)*c); b2=a*((a+1)+(a-1)*c-t);
        a0=(a+1)-(a-1)*c+t; a1=2*((a-1)-(a+1)*c); a2=(a+1)-(a-1)*c-t;
    }
    return (Coeff){b0/a0,b1/a0,b2/a0,a1/a0,a2/a0};
}
static LV2_Handle instantiate(const LV2_Descriptor *d,double rate,const char *path,const LV2_Feature *const *features) {
    (void)d; (void)path; (void)features;
    if (!isfinite(rate) || rate < 1000 || rate > 768000) return NULL;
    EQ *eq=calloc(1,sizeof(EQ));
    if (eq) {
        eq->rate=rate; eq->gain=0; for(int b=0;b<BANDS;b++) eq->coeff[b]=identity();
        /* Setup-only telemetry for our controller. No logging in run(). */
        const char *report=getenv("EQXEAR_REPORT_RATE");
        if(report && strcmp(report,"1")==0) fprintf(stderr,"EQXEAR_SAMPLE_RATE=%.0f\n",rate);
    }
    return eq;
}
static void connect_port(LV2_Handle instance,uint32_t port,void *data) {
    if(port<PORTS) ((EQ *)instance)->ports[port]=data;
}
static double control(EQ *eq,int index,double fallback) {
    float *p=eq->ports[4+index];
    return p && isfinite(*p)?*p:fallback;
}
static void run(LV2_Handle instance,uint32_t count) {
    EQ *eq=instance;
    int changed=!eq->initialized;
    for(int i=0;i<CONTROLS;i++) {
        float value=control(eq,i,0);
        if(value!=eq->last[i]) changed=1;
        eq->last[i]=value;
    }
    if(changed) {
        int bypass=control(eq,2,0)>.5;
        eq->target_gain=bypass?1:(control(eq,1,0)>.5?0:pow(10,clamp(control(eq,0,0),-96,18)/20));
        for(int b=0;b<BANDS;b++) {
            int i=3+b*4;
            eq->target[b]=coefficients(bypass?0:(int)clamp(control(eq,i,0),0,3),
                control(eq,i+1,1000),clamp(control(eq,i+2,0),-12,12),clamp(control(eq,i+3,1),.3,10),eq->rate);
        }
        eq->remaining=(unsigned)fmax(1,eq->rate*.01);
        eq->initialized=1;
    }
    if (!eq->remaining && eq->last[2]>.5f) {
        for (int ch=0;ch<2;ch++) {
            if (!eq->ports[ch+2]) continue;
            if (eq->ports[ch]) {
                for (uint32_t n=0;n<count;n++) {
                    float sample=eq->ports[ch][n];
                    eq->ports[ch+2][n]=isfinite(sample)?sample:0;
                }
            }
            else memset(eq->ports[ch+2],0,count*sizeof(float));
        }
        // No stale filter history when processing resumes after passthrough.
        memset(eq->history,0,sizeof(eq->history));
        return;
    }
    for(uint32_t n=0;n<count;n++) {
        if(eq->remaining) {
            double step=1.0/eq->remaining;
            eq->gain+=(eq->target_gain-eq->gain)*step;
            for(int b=0;b<BANDS;b++) {
#define RAMP(member) eq->coeff[b].member+=(eq->target[b].member-eq->coeff[b].member)*step
                RAMP(b0); RAMP(b1); RAMP(b2); RAMP(a1); RAMP(a2);
#undef RAMP
            }
            eq->remaining--;
        }
        for(int ch=0;ch<2;ch++) {
            double input=eq->ports[ch]?eq->ports[ch][n]:0;
            if (!isfinite(input)) {
                memset(eq->history[ch],0,sizeof(eq->history[ch]));
                if (eq->ports[ch+2]) eq->ports[ch+2][n]=0;
                continue;
            }
            double value=input*eq->gain;
            for(int b=0;b<BANDS;b++) {
                Coeff c=eq->coeff[b]; History *h=&eq->history[ch][b];
                double out=c.b0*value+c.b1*h->x1+c.b2*h->x2-c.a1*h->y1-c.a2*h->y2;
                if (!isfinite(out)) {
                    memset(eq->history[ch],0,sizeof(eq->history[ch]));
                    value=0;
                    break;
                }
                h->x2=h->x1; h->x1=value; h->y2=h->y1; h->y1=fabs(out)<1e-25?0:out;
                value=h->y1;
            }
            // Mute is exact once its ramp ends, including resonant filter tails.
            if(!eq->remaining && eq->target_gain==0) value=0;
            // Float conversion can overflow even when the double is finite.
            // Do not clip intentional boosts at unity; only reject values that
            // the output format cannot represent and clear the affected channel.
            if (!isfinite(value) || fabs(value)>FLT_MAX) {
                memset(eq->history[ch],0,sizeof(eq->history[ch]));
                value=0;
            }
            if(eq->ports[ch+2]) eq->ports[ch+2][n]=(float)value;
        }
    }
}
static void cleanup(LV2_Handle instance) { free(instance); }
static const LV2_Descriptor descriptor={URI,instantiate,connect_port,NULL,run,NULL,cleanup,NULL};
LV2_SYMBOL_EXPORT const LV2_Descriptor *lv2_descriptor(uint32_t index) { return index==0?&descriptor:NULL; }
