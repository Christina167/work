#include "port.h"
#include "mb.h"
#include "mbport.h"

BOOL xMBPortSerialInit(UCHAR ucPORT,
                       ULONG ulBaudRate,
                       UCHAR ucDataBits,
                       eMBParity eParity)
{
    /*
     * 当前固定使用：
     * USART1
     * 9600 baud
     * 8 data bits
     * no parity
     */

    (void)ucPORT;

    if (ulBaudRate != 9600UL)
    {
        return FALSE;
    }

    if (ucDataBits != 8U)
    {
        return FALSE;
    }

    if (eParity != MB_PAR_NONE)
    {
        return FALSE;
    }

    /*
     * USART1 已由 CubeMX 初始化。
     * 此处只关闭 FreeModbus 尚未启用的中断。
     */
    __HAL_UART_DISABLE_IT(&huart1, UART_IT_RXNE);
    __HAL_UART_DISABLE_IT(&huart1, UART_IT_TXE);

    return TRUE;
}

void vMBPortSerialEnable(BOOL xRxEnable, BOOL xTxEnable)
{
    /*
     * 先全部关闭，保证接收和发送状态切换明确。
     */
    __HAL_UART_DISABLE_IT(&huart1, UART_IT_RXNE);
    __HAL_UART_DISABLE_IT(&huart1, UART_IT_TXE);

    if (xRxEnable == TRUE)
    {
        __HAL_UART_ENABLE_IT(&huart1, UART_IT_RXNE);
    }

    if (xTxEnable == TRUE)
    {
        /*
         * TXE 当前通常已经为 1。
         * 打开 TXEIE 后会立即进入 USART1 中断，
         * FreeModbus 将发送第一个字节。
         */
        __HAL_UART_ENABLE_IT(&huart1, UART_IT_TXE);
    }
}

BOOL xMBPortSerialPutByte(CHAR ucByte)
{
    huart1.Instance->DR = (uint8_t)ucByte;
    return TRUE;
}

BOOL xMBPortSerialGetByte(CHAR *pucByte)
{
    *pucByte = (CHAR)(huart1.Instance->DR & 0xFFU);
    return TRUE;
}

void xMBPortSerialClose(void)
{
    __HAL_UART_DISABLE_IT(&huart1, UART_IT_RXNE);
    __HAL_UART_DISABLE_IT(&huart1, UART_IT_TXE);
}

void vMBPortClose(void)
{
    xMBPortSerialClose();
}

/*
 * USART1_IRQHandler 调用这些包装函数。
 * 它们再调用 FreeModbus 在 eMBInit() 中安装的状态机回调。
 */
void prvvUARTRxISR(void)
{
    if (pxMBFrameCBByteReceived != NULL)
    {
        (void)pxMBFrameCBByteReceived();
    }
}

void prvvUARTTxReadyISR(void)
{
    if (pxMBFrameCBTransmitterEmpty != NULL)
    {
        (void)pxMBFrameCBTransmitterEmpty();
    }
}
