 #ifndef __DELAY_H
#define __DELAY_H 			   
//#include "sys.h"

#define u8 unsigned char
#define u16 unsigned int
#define u32 unsigned long

void delay_init(void);
void delay_ms(u16 nms);
void delay_us(u32 nus);

#endif





























