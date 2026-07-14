#ifndef _PORT_H
#define _PORT_H

#include "stm32f1xx_hal.h"
#include <stdint.h>

/* C/C++ 兼容宏 */
#ifdef __cplusplus
#define PR_BEGIN_EXTERN_C extern "C" {
#define PR_END_EXTERN_C   }
#else
#define PR_BEGIN_EXTERN_C
#define PR_END_EXTERN_C
#endif

#define INLINE inline

/*
 * 阶段 1 为裸机单线程。
 * FreeModbus 在关键区中关闭/打开全局中断。
 */
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
#define TRUE  1
#endif

#ifndef FALSE
#define FALSE 0
#endif

extern UART_HandleTypeDef huart1;
extern TIM_HandleTypeDef htim3;

/* STM32 中断函数调用的包装函数 */
void prvvUARTTxReadyISR(void);
void prvvUARTRxISR(void);
void TIMERExpiredISR(void);

#endif