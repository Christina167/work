#ifndef _PORT_H
#define _PORT_H

#include "stm32f1xx_hal.h"
#include <stdint.h>

#define INLINE inline

#define PR_BEGIN_EXTERN_C
#define PR_END_EXTERN_C

#define ENTER_CRITICAL_SECTION()  __disable_irq()
#define EXIT_CRITICAL_SECTION()   __enable_irq()

typedef uint8_t  BOOL;
typedef uint8_t  UCHAR;
typedef char     CHAR;
typedef uint16_t USHORT;
typedef int16_t  SHORT;
typedef uint32_t ULONG;
typedef int32_t  LONG;

#ifndef TRUE
#define TRUE 1
#endif

#ifndef FALSE
#define FALSE 0
#endif

extern UART_HandleTypeDef huart1;
extern TIM_HandleTypeDef htim3;

void prvvUARTTxReadyISR(void);
void prvvUARTRxISR(void);
void TIMERExpiredISR(void);

#endif